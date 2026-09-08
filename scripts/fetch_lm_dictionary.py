#!/usr/bin/env python3
"""Loughran-McDonald 정식 마스터 사전을 받아 data/lm_dictionary.csv 로 둔다.

## 왜 스크립트인가 (레포에 커밋하지 않는 이유)

사전 파일은 Notre Dame SRAF가 배포하는 저작물이다. 연구 목적 사용은 의도된
용도지만 **재배포는 별개 문제**이고 이 레포는 public이다. 그래서 파일은
`.gitignore` 로 빼고, 대신 **어떤 파일을 썼는지 증명하는 메타데이터**
(`data/lm_dictionary.meta.json`: 출처 URL·수신 시각·sha256·단어 수)를 커밋한다.
재현하려는 사람은 이 스크립트를 돌려 같은 해시가 나오는지 확인하면 된다.

## 왜 중요한가

`src/process/sentiment.py` 는 이 파일이 없으면 **코드에 내장된 축약 서브셋**으로
조용히 내려간다(부정 ~90단어 대 정식 ~2,300단어). README와 CLAUDE.md는
"Loughran-McDonald 사전 baseline"이라고 적어 왔는데, 파일이 없는 동안 실제로
돌던 것은 LM 서브셋이었다. 논문화 전에 반드시 정식 사전으로 맞춰야 한다.

## 사전을 바꾸면 과거와 미래가 다른 척도가 된다

**바꾼 뒤에는 감성 점수의 시계열이 두 구간으로 갈린다.** 그래서
`data/live/` 의 freeze된 스냅샷은 각자 그 시점 사전으로 매긴 값을 유지하고,
manifest에 사전 해시가 함께 남는다. latest 층을 다시 매길지는 별도 판단이다
(전량 재스코어링은 그 자체로 기록을 덮는 행위다 -- CLAUDE.md 7장 17번 (d)).

사용:
    python scripts/fetch_lm_dictionary.py            # 받아서 저장
    python scripts/fetch_lm_dictionary.py --check    # 현재 파일 상태만 출력
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DATA_DIR, load_config  # noqa: E402

CSV_PATH = DATA_DIR / "lm_dictionary.csv"
META_PATH = DATA_DIR / "lm_dictionary.meta.json"

# 공식 배포 페이지. 파일은 Google Drive에 올라가 있고 ID가 갱신될 수 있으므로
# 페이지에서 다시 찾을 수 있게 URL을 함께 둔다.
SRAF_PAGE = "https://sraf.nd.edu/loughranmcdonald-master-dictionary/"
DEFAULT_FILE_ID = "1iq2RUf8qGFEAk1g8wQntP3habOnR3fXF"  # Master Dictionary CSV (2026-03 기준)
UA = "us-market-daily (research; contact via repo)"

REQUIRED = ("word", "negative", "positive", "uncertainty")


def _drive_download(file_id: str) -> bytes:
    """Google Drive 직접 다운로드. 용량이 크면 확인 페이지를 거친다."""
    s = requests.Session()
    url = "https://drive.google.com/uc?export=download"
    r = s.get(url, params={"id": file_id}, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    head = r.content[:400].lstrip().lower()
    if not head.startswith(b"<"):                      # 이미 파일이다
        return r.content

    # 바이러스 검사 확인 페이지. 토큰을 찾아 다시 요청한다.
    text = r.text
    token = None
    m = re.search(r'name="confirm"\s+value="([^"]+)"', text) or \
        re.search(r"[?&]confirm=([\w-]+)", text)
    if m:
        token = m.group(1)
    params = {"id": file_id, "export": "download"}
    if token:
        params["confirm"] = token
    m2 = re.search(r'name="uuid"\s+value="([^"]+)"', text)
    if m2:
        params["uuid"] = m2.group(1)
    r2 = s.get("https://drive.usercontent.google.com/download", params=params,
               headers={"User-Agent": UA}, timeout=180)
    r2.raise_for_status()
    return r2.content


def _summarize(df: pd.DataFrame) -> dict:
    cols = {c.lower(): c for c in df.columns}
    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise ValueError(f"필수 열이 없다: {missing} (받은 열: {list(df.columns)[:12]})")
    out = {"rows": int(len(df))}
    for c in ("negative", "positive", "uncertainty", "litigious", "strong_modal", "weak_modal"):
        if c in cols:
            out[c] = int((pd.to_numeric(df[cols[c]], errors="coerce").fillna(0) > 0).sum())
    return out


def _write_meta(url: str, blob: bytes, summary: dict) -> dict:
    meta = {
        "source_page": SRAF_PAGE,
        "source_url": url,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
        **summary,
        "note": ("파일 자체는 재배포하지 않는다(.gitignore). 재현하려면 이 스크립트를 "
                 "돌려 같은 sha256이 나오는지 확인할 것."),
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True),
                         encoding="utf-8")
    return meta


def _check() -> int:
    if not CSV_PATH.exists():
        print(f"없음: {CSV_PATH}")
        print("  -> python scripts/fetch_lm_dictionary.py 로 받을 것.")
        print("     없으면 sentiment.py 가 코드 내장 축약 서브셋으로 동작한다.")
        return 1
    blob = CSV_PATH.read_bytes()
    df = pd.read_csv(io.BytesIO(blob))
    print(f"파일: {CSV_PATH} ({len(blob):,} bytes)")
    print(f"sha256: {hashlib.sha256(blob).hexdigest()}")
    print(f"요약: {_summarize(df)}")
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        same = meta.get("sha256") == hashlib.sha256(blob).hexdigest()
        print(f"메타: {META_PATH.name} · 수신 {meta.get('downloaded_at_utc')} "
              f"· 해시 {'일치' if same else '불일치(파일이 바뀌었다)'}")
        if not same:
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-id", default=None, help="Google Drive 파일 ID (기본: 공식 CSV)")
    ap.add_argument("--url", default=None, help="직접 URL (Drive 대신 사내 미러 등)")
    ap.add_argument("--check", action="store_true", help="받지 않고 현재 상태만 확인")
    args = ap.parse_args()

    if args.check:
        return _check()

    cfg = load_config()
    file_id = args.file_id or str(cfg.get_path("sentiment.lm_file_id", "") or DEFAULT_FILE_ID)
    url = args.url or str(cfg.get_path("sentiment.lm_dictionary_url", "") or "")

    if url:
        print(f"내려받는 중: {url}")
        r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
        r.raise_for_status()
        blob, src = r.content, url
    else:
        print(f"내려받는 중: Google Drive id={file_id}  (출처 {SRAF_PAGE})")
        blob = _drive_download(file_id)
        src = f"https://drive.google.com/file/d/{file_id}/view"

    try:
        df = pd.read_csv(io.BytesIO(blob))
    except Exception as e:
        print(f"CSV로 읽히지 않는다: {e}")
        print("  Drive 링크가 바뀌었을 수 있다. 위 출처 페이지에서 CSV 링크를 확인하고")
        print("  --file-id 또는 --url 로 지정할 것.")
        return 1

    try:
        summary = _summarize(df)
    except ValueError as e:
        print(f"형식 확인 실패: {e}")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CSV_PATH.write_bytes(blob)
    meta = _write_meta(src, blob, summary)

    print()
    print(f"저장: {CSV_PATH} ({len(blob):,} bytes)")
    print(f"sha256: {meta['sha256']}")
    print(f"단어: 전체 {summary['rows']:,} · 부정 {summary.get('negative')} "
          f"· 긍정 {summary.get('positive')} · 불확실 {summary.get('uncertainty')}")
    print()
    print("사전이 바뀌면 감성 점수의 척도가 바뀐다. freeze된 스냅샷은 각자 그 시점")
    print("사전으로 매긴 값을 유지하고, manifest에 사전 해시가 함께 남는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
