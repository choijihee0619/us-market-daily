#!/usr/bin/env python3
"""과거 세션의 live 스냅샷을 git 이력에서 소급 복원한다.

## 왜 가능한가

`data/*.parquet` 을 매일 전량 커밋해 왔다. git 이력 비대의 원인이지만 여기서는
축복이다 -- **각 세션이 처음 계산된 시점의 파일이 이력에 통째로 남아 있다.**
그 시점 파일에서 그 세션 행만 떼어내면 재실행으로 덮이기 전의 값이 나온다.

`src/freeze.py` 도입(2026-09-08) 이전 세션들은 보호 장치 없이 21/24가 재실행으로
덮였다. 지금 `data/*.parquet` 에 남아 있는 값은 마지막 실행 값이지 최초 실행 값이
아니다. 이 스크립트는 그 최초 값을 되찾는다.

## 어떻게 앵커 커밋을 고르는가

커밋 메시지(`daily: 2026-09-04`)를 파싱하지 않는다. 메시지는 사람이 바꿀 수 있고
실제로 형식이 다른 것들이 있다(수동 복구 커밋 등). 대신 **데이터로 찾는다.**
표를 건드린 커밋을 오래된 순으로 훑으면서 각 세션 날짜가 **처음 등장한 커밋**을
그 표의 앵커로 잡는다.

**표마다 따로 잡는다.** 한 커밋으로 통일하지 않는 이유는 실측 때문이다 --
2026-08-03과 08-12는 signals가 먼저 들어오고 residuals는 다음 커밋에 들어왔다.
그 시점에 잔차 추정이 빈 결과를 냈고(팩터 미도착 등) 나중 실행에서 채워진 것이다.
한 커밋으로 묶으면 둘 중 하나가 통째로 비거나, 다른 하나가 필요 이상으로 늦은
값이 된다. 표별 최초값을 각각 가져오는 쪽이 오염이 가장 적다.

  news, signals : signals 앵커 (뉴스가 그 신호를 만든 입력이므로 같이 간다)
  residuals     : residuals 앵커
  scorecard     : scorecard 앵커

앵커 세 개를 manifest 에 전부 남긴다.

## 한계 -- 반드시 같이 읽을 것

1. **커밋 시각 ≠ 수집 시각.** 파이프라인이 돌고 몇 분 뒤에 커밋된다.
   `reconstructed_from.committed_at_utc` 로 남기되 그게 수집 시각은 아니다.
2. **최초 커밋이 최초 실행이라는 가정.** 한 실행이 커밋 없이 끝났다면(변경 없음)
   그 실행의 값은 이력에 없다.
3. **`provenance="reconstructed"` 다.** `live` 가 아니다. 실시간성을 요구하는
   분석에서는 이 스냅샷들을 빼거나 따로 표시해서 쓸 것.
4. 이미 freeze된 세션은 건드리지 않는다. 불변 층은 덮어쓰지 않는다.

사용:
    python scripts/restore_live_snapshots.py --dry-run   # 무엇을 복원할지만 출력
    python scripts/restore_live_snapshots.py             # 실제 복원
"""
from __future__ import annotations

import argparse
import io
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import freeze as FRZ  # noqa: E402
from src.calendar_utils import news_window  # noqa: E402
from src.collect import news as N  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("restore")

ROOT = Path(__file__).resolve().parents[1]
ANCHOR_TABLES = ("signals", "residuals", "scorecard")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(ROOT)).decode("utf-8", "replace")


def _commits_touching(path: str) -> list[tuple[str, str, str]]:
    """(sha, committed_at_iso, subject) 를 오래된 순으로."""
    out = _git("log", "--reverse", "--format=%H\t%cI\t%s", "--", path)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, iso, subj = line.split("\t", 2)
        rows.append((sha, iso, subj))
    return rows


def _read_at(sha: str, path: str) -> pd.DataFrame:
    """특정 커밋 시점의 parquet 을 메모리로 읽는다. 없으면 빈 DF."""
    try:
        blob = subprocess.check_output(
            ["git", "show", f"{sha}:{path}"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return pd.DataFrame()
    if not blob:
        return pd.DataFrame()
    try:
        return pd.read_parquet(io.BytesIO(blob))
    except Exception as e:                                    # pragma: no cover
        log.warning("%s:%s 읽기 실패: %s", sha[:8], path, e)
        return pd.DataFrame()


def _dates_in(df: pd.DataFrame) -> set[pd.Timestamp]:
    if df.empty or "date" not in df.columns:
        return set()
    return set(pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize())


def find_anchors() -> dict[str, dict[pd.Timestamp, dict]]:
    """표 -> 세션 -> 그 세션이 그 표에 처음 등장한 커밋."""
    anchors: dict[str, dict[pd.Timestamp, dict]] = {t: {} for t in ANCHOR_TABLES}
    for table in ANCHOR_TABLES:
        path = f"data/{table}.parquet"
        commits = _commits_touching(path)
        log.info("%s -- 커밋 %d개 훑는 중", path, len(commits))
        for sha, iso, subj in commits:
            for d in sorted(_dates_in(_read_at(sha, path))):
                anchors[table].setdefault(
                    d, {"sha": sha, "committed_at": iso, "subject": subj})
    return anchors


def restore_one(session: pd.Timestamp, anchors: dict, *, dry_run: bool) -> dict:
    a_sig = anchors["signals"].get(session)
    a_res = anchors["residuals"].get(session)
    a_sc = anchors["scorecard"].get(session)

    start, end = news_window(session)
    news_win, signals = pd.DataFrame(), pd.DataFrame()
    if a_sig:
        news_all = _read_at(a_sig["sha"], "data/news.parquet")
        news_win = N.filter_window(news_all, start, end) if not news_all.empty else pd.DataFrame()
        signals = _read_at(a_sig["sha"], "data/signals.parquet")
    resid = _read_at(a_res["sha"], "data/residuals.parquet") if a_res else pd.DataFrame()

    scorecard = None
    if a_sc:
        sc_df = _read_at(a_sc["sha"], "data/scorecard.parquet")
        if not sc_df.empty and "date" in sc_df.columns:
            m = pd.to_datetime(sc_df["date"]).dt.tz_localize(None).dt.normalize() == session
            if m.any():
                scorecard = sc_df[m].iloc[0].to_dict()

    def _n(df):
        if df.empty or "date" not in df.columns:
            return len(df)
        return int((pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize() == session).sum())

    counts = {"news": len(news_win), "signals": _n(signals), "residuals": _n(resid),
              "sig_sha": (a_sig or {}).get("sha", "")[:8],
              "res_sha": (a_res or {}).get("sha", "")[:8]}
    if dry_run:
        return counts

    def _src(a):
        return None if not a else {"commit": a["sha"][:12],
                                   "committed_at_utc": a["committed_at"],
                                   "subject": a["subject"]}

    FRZ.freeze_session(
        session,
        news=news_win, signals=signals, residuals=resid, scorecard=scorecard,
        news_window=(start, end),
        provenance="reconstructed",
        meta={
            "reconstructed_from": {
                "news_signals": _src(a_sig),
                "residuals": _src(a_res),
                "scorecard": _src(a_sc),
            },
            "caveat": ("커밋 시각은 수집 시각이 아니다. 최초 커밋 == 최초 실행이라는 "
                       "가정에 의존한다. 표마다 앵커 커밋이 다를 수 있다. "
                       "실시간(live) 기록이 아니다."),
        },
        repo=ROOT,
    )
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="복원 대상만 출력하고 쓰지 않는다")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만 처리 (시험용)")
    args = ap.parse_args()

    anchors = find_anchors()
    sessions = sorted(set().union(*(set(v) for v in anchors.values())))
    if not sessions:
        log.error("앵커 커밋을 찾지 못했다. git 이력에 data/signals.parquet 이 있는지 확인할 것.")
        return 1

    todo, skipped = [], []
    for session in sessions:
        (skipped if FRZ.is_frozen(session) else todo).append(session)
    if args.limit:
        todo = todo[: args.limit]

    print()
    print(f"이력에서 발견한 세션 {len(sessions)}개 · 이미 freeze {len(skipped)}개 · 복원 대상 {len(todo)}개")
    print("=" * 96)
    print(f"{'세션':<12} {'커밋시각(UTC)':<21} {'news':>6} {'signals':>8} {'resid':>7}  "
          f"{'sig커밋':<9} {'res커밋':<9}")
    print("-" * 96)

    total = {"news": 0, "signals": 0, "residuals": 0}
    for session in todo:
        c = restore_one(session, anchors, dry_run=args.dry_run)
        for k in total:
            total[k] += c[k]
        a = anchors["signals"].get(session) or anchors["residuals"].get(session)
        mark = "" if c["sig_sha"] == c["res_sha"] else "  <- 앵커 다름"
        print(f"{session.date()!s:<12} {a['committed_at'][:19]:<21} "
              f"{c['news']:>6} {c['signals']:>8} {c['residuals']:>7}  "
              f"{c['sig_sha']:<9} {c['res_sha']:<9}{mark}")

    print("-" * 96)
    print(f"{'합계':<12} {'':<21} {total['news']:>6} {total['signals']:>8} {total['residuals']:>7}")
    if skipped:
        print(f"\n건너뜀(이미 freeze): {', '.join(str(s.date()) for s in skipped)}")
    if args.dry_run:
        print("\n--dry-run 이라 아무것도 쓰지 않았다.")
    else:
        print(f"\n{len(todo)}개 세션을 data/live/ 에 provenance=reconstructed 로 기록했다.")
        print("실시간 기록이 아니다. 분석에서 live 와 섞어 쓰지 말 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
