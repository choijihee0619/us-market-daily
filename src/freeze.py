"""세션 freeze — 실시간(out-of-sample) 기록의 불변 사본.

## 왜 필요한가

`storage.upsert`는 이름과 달리 append-only가 아니다. 같은 키가 다시 들어오면
`keep="last"`로 덮는다. 그건 FRED 잠정치→확정치, Ken French 팩터 소급 갱신을
흡수하려는 **의도된 설계**다. 문제는 그 설계가 뉴스 신호에도 그대로 적용된다는
것이다. 같은 세션을 다시 돌리면 그 세션의 news·signals·scorecard가 재실행 시점
데이터로 바뀐다.

그리고 재실행은 예외가 아니라 상수다. cron이 `1-5`(월~금 UTC)라 주말·공휴일에는
`last_completed_session()`이 같은 날을 계속 돌려준다. 실측(2026-09-08):
**고유 세션 24개에 daily 커밋 51건, 21개 세션이 2회 이상 재처리됐다.**

2026-09-04 세션을 커밋 4개 버전으로 비교한 결과다.

| | 최초(09-04) | 09-05 | 09-07 | 09-08 |
|---|---|---|---|---|
| 창 내 뉴스 | 1,150 | 1,211 | 1,266 | 1,266 |
| 신호 종목 | 674 | 711 | **754** | 754 |
| 기존 기사 novelty 변경 | — | 84건 | 55건 | 1건 |
| 원시 sentiment 변경 | — | **0건** | 0건 | 0건 |
| scorecard spread_bp | 38.0178 | 38.0178 | **37.3037** | 37.3037 |

**원시 LM 감성은 한 건도 안 변했다.** 스코어러는 결정적이다. 변한 건 전부
history 의존 항목이다 -- `score_dataframe(nw, hist, …)`의 novelty가 확장된
history와 다시 비교되면서 **이미 저장된 과거 기사의 feature까지 소급 변경된다.**
신규 행 추가가 아니라 기존 행 변조이고, 그 끝에서 4번 블록 채점 숫자가 바뀐다.

이 프로젝트의 존재 이유가 "예측을 팔지 않고 기록을 남긴다"이므로, 기록이 사후에
바뀌면 남는 게 없다.

## 무엇을 보장하는가

`data/live/{세션}/` 은 **한 번 쓰이면 절대 바뀌지 않는다.** 이미 있는 세션에
다시 쓰려 하면 `AlreadyFrozenError`를 던진다. 덮어쓰기 옵션은 만들지 않았다 --
"필요하면 덮을 수 있는 불변 층"은 불변이 아니다.

`data/*.parquet`(latest 층)은 지금처럼 계속 갱신된다. 두 층의 역할이 다르다.
  - `data/live/`   : 그날 실제로 무엇을 알고 있었나 (ex-ante). 절대 불변
  - `data/*.parquet`: 지금 시점의 최선 추정치 (ex-post). 소급 갱신 허용

나중에 `Signal^live` 와 `Signal^revised` 를 비교할 수 있게 되는데, 그 차이 자체가
연구 대상이다(뉴스 기반 신호의 사후 재구성이 실시간 신호를 얼마나 부풀리는가).

## 원자성

임시 디렉터리에 전부 쓴 다음 `rename` 한 번으로 확정한다. rename이 커밋 지점이라
중간에 죽어도 **반쯤 만들어진 스냅샷이 보이지 않는다.** 이게 중요한 이유는
`is_frozen`이 곧 재실행 가드이기 때문이다. 껍데기 디렉터리가 남아 "이미 freeze됨"
으로 판정되면 그 세션은 영영 기록되지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR, ROOT

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST = "manifest.json"

# 스냅샷에 담는 표. 이름 -> 설명
TABLES = {
    "news": "세션 창 안의 기사. 감성·novelty·토픽이 그날 계산된 값 그대로다",
    "signals": "종목별 일간 집계 신호. 이 세션의 행만",
    "residuals": "팩터 회귀 잔차. 이 세션의 행만",
}


class AlreadyFrozenError(RuntimeError):
    """이미 freeze된 세션에 다시 쓰려 했다. 불변 층은 덮어쓰지 않는다."""


def live_root(base: Path | None = None) -> Path:
    return Path(base) if base is not None else DATA_DIR / "live"


def snapshot_dir(session, base: Path | None = None) -> Path:
    d = pd.Timestamp(session).strftime("%Y-%m-%d")
    return live_root(base) / d


def is_frozen(session, base: Path | None = None) -> bool:
    """manifest.json 이 있어야 freeze된 것으로 본다.

    디렉터리 존재만으로 판정하지 않는 이유는 위 '원자성' 절과 같다. rename 전
    임시 디렉터리나 사람이 만든 빈 디렉터리를 freeze로 오인하면 그 세션의 기록이
    영구히 비게 된다.
    """
    return (snapshot_dir(session, base) / MANIFEST).exists()


def read_manifest(session, base: Path | None = None) -> dict | None:
    p = snapshot_dir(session, base) / MANIFEST
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                   # pragma: no cover
        log.warning("manifest 읽기 실패 %s: %s", p, e)
        return None


def list_frozen(base: Path | None = None) -> list[str]:
    root = live_root(base)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / MANIFEST).exists())


def _git_sha(repo: Path | None = None) -> tuple[str | None, bool]:
    """(short sha, dirty). 실패해도 freeze를 막지 않는다."""
    cwd = str(repo or ROOT)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=cwd, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None, False
    try:
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=cwd, stderr=subprocess.DEVNULL
        ).decode().strip())
    except Exception:
        dirty = False
    return sha, dirty


def _sha1_file(p: Path) -> str | None:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()[:16]
    except Exception:
        return None


def _session_rows(df: pd.DataFrame | None, session) -> pd.DataFrame:
    """세션 날짜 행만 남긴다. date 열이 없으면 그대로 둔다(news는 창으로 이미 잘려 있다)."""
    if df is None or df.empty:
        return pd.DataFrame()
    if "date" not in df.columns:
        return df.copy()
    d = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df[d == pd.Timestamp(session).normalize()].copy()


def _jsonable(v: Any) -> Any:
    """manifest에 넣기 전에 numpy/pandas 스칼라를 파이썬 기본형으로 낮춘다."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (pd.Timestamp, datetime)):
        return str(v)
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    if hasattr(v, "item"):                    # numpy 스칼라
        try:
            return v.item()
        except Exception:
            pass
    return str(v)


def freeze_session(
    session,
    *,
    news: pd.DataFrame | None = None,
    signals: pd.DataFrame | None = None,
    residuals: pd.DataFrame | None = None,
    scorecard: dict | None = None,
    news_window: tuple | None = None,
    provenance: str = "live",
    meta: dict | None = None,
    base: Path | None = None,
    repo: Path | None = None,
) -> Path:
    """세션의 불변 스냅샷을 만든다. 이미 있으면 AlreadyFrozenError.

    provenance
      "live"          : 그 세션을 처음 처리하면서 찍었다. 실시간 기록이다
      "late"          : 이미 처리된 적 있는 세션을 뒤늦게 찍었다. **실시간이 아니다.**
                        freeze 기능 도입(2026-09-08) 이전 세션이 여기 해당한다
      "reconstructed" : git 이력 등에서 소급 복원했다

    이 값을 남기는 이유는 계층 2가 계층 1보다 먼저라는 원칙과 같다
    (`docs/SESSION_GAPS.md` 5절). **표시 없는 소급 기록은 실시간 기록을 되돌릴 수
    없게 오염시킨다.** 나중에 분석할 때 `provenance == "live"` 만 걸러 써야 한다.
    """
    if provenance not in ("live", "late", "reconstructed"):
        raise ValueError(f"provenance 값이 이상하다: {provenance}")

    dest = snapshot_dir(session, base)
    if (dest / MANIFEST).exists():
        raise AlreadyFrozenError(
            f"{pd.Timestamp(session).date()} 세션은 이미 freeze되어 있다: {dest}"
        )

    sess = pd.Timestamp(session).normalize()
    frames = {
        "news": (news.copy() if news is not None and not news.empty else pd.DataFrame()),
        "signals": _session_rows(signals, sess),
        "residuals": _session_rows(residuals, sess),
    }

    sha, dirty = _git_sha(repo)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session": str(sess.date()),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": provenance,
        "git_sha": sha,
        "git_dirty": dirty,
        "config_sha1": _sha1_file(ROOT / "config.yaml"),
        # Actions에서 돌았는지 사람이 로컬에서 돌렸는지. 재현 조건이 다르다
        "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") else "local",
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "news_window_utc": (
            [str(news_window[0]), str(news_window[1])] if news_window else None
        ),
        "rows": {k: int(len(v)) for k, v in frames.items()},
        "scorecard": _jsonable(scorecard) if scorecard else None,
        "tables": {k: TABLES[k] for k in frames},
    }
    if meta:
        manifest["meta"] = _jsonable(meta)

    # 임시 디렉터리에 전부 쓰고 rename 한 번으로 확정한다(원자성).
    root = live_root(base)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".tmp-{sess.strftime('%Y-%m-%d')}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        for name, df in frames.items():
            df.to_parquet(tmp / f"{name}.parquet", index=False)
        (tmp / MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        try:
            tmp.rename(dest)
        except OSError:
            # 같은 순간에 다른 실행이 먼저 rename했을 수 있다. 먼저 쓴 쪽이 이긴다.
            if (dest / MANIFEST).exists():
                raise AlreadyFrozenError(
                    f"{sess.date()} 세션이 방금 다른 실행에서 freeze됐다: {dest}"
                )
            raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    log.info("freeze %s -> %s (news=%d signals=%d residuals=%d, provenance=%s)",
             sess.date(), dest, manifest["rows"]["news"], manifest["rows"]["signals"],
             manifest["rows"]["residuals"], provenance)
    return dest


def read_frozen(session, table: str, base: Path | None = None) -> pd.DataFrame:
    """불변 스냅샷에서 표 하나를 읽는다. 없으면 빈 DataFrame."""
    if table not in TABLES:
        raise ValueError(f"모르는 표: {table} (가능: {list(TABLES)})")
    p = snapshot_dir(session, base) / f"{table}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)
