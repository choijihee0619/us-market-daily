#!/usr/bin/env python3
"""발행 후 실제 URL 기록 (매일 15초).

티스토리 포스트 주소가 '숫자'라서 URL은 발행하는 순간 결정된다.
발행 직후 이 명령을 한 번 돌리면
  - posts/*.md 의 canonical_url
  - 네이버 요약본의 원문 링크
  - data/post_urls.json (애널리틱스가 경로->날짜 매핑에 사용)
가 한꺼번에 맞춰진다.

사용:
    python scripts/link_post.py https://dailyresidualnote.com/123
    python scripts/link_post.py https://dailyresidualnote.com/124 --date 2026-07-28
    python scripts/link_post.py https://dailyresidualnote.com/125 --kind weekly
    python scripts/link_post.py --list
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calendar_utils import last_completed_session  # noqa: E402
from src.config import OUT_DIR, load_config  # noqa: E402
from src.process.weekly_stats import week_bounds  # noqa: E402
from src.publish import url_registry as REG  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("link_post")
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", help="발행된 글의 전체 URL")
    ap.add_argument("--date", help="대상 거래일 (YYYY-MM-DD). 비우면 최근 세션")
    ap.add_argument("--kind", default="daily", choices=["daily", "weekly"])
    ap.add_argument("--list", action="store_true", help="기록된 매핑 출력")
    args = ap.parse_args()

    cfg = load_config()
    site = str(cfg.get_path("report.site_url", "") or "").rstrip("/")

    if args.list:
        reg = REG.load(ROOT)
        if not reg:
            print("기록된 매핑이 없다.")
            return 0
        print(f"{'날짜':<12} {'종류':<8} {'경로':<12} URL")
        for k in sorted(reg):
            e = reg[k]
            print(f"{e['date']:<12} {e['kind']:<8} {e['path']:<12} {e['url']}")
        print(f"\n총 {len(reg)}건")
        return 0

    if not args.url:
        ap.error("URL이 필요하다 (또는 --list)")

    url = args.url.strip()
    if not url.startswith("http"):
        url = f"{site}/{url.lstrip('/')}"     # 숫자만 넣어도 되게
        log.info("URL 보정: %s", url)
    if site and not url.startswith(site):
        log.warning("URL이 설정된 도메인(%s)과 다르다. 그대로 진행한다.", site)

    if args.date:
        date = pd.Timestamp(args.date).normalize()
    elif args.kind == "weekly":
        anchor = last_completed_session() or pd.Timestamp.today()
        date, _ = week_bounds(anchor)
    else:
        date = last_completed_session()
        if date is None:
            log.error("최근 세션을 특정할 수 없다. --date 를 지정할 것.")
            return 1

    entry = REG.record(ROOT, date, url, args.kind)

    fm = REG.patch_front_matter(ROOT, date, url, args.kind)
    nv = REG.patch_naver(OUT_DIR, date, url, args.kind)

    print()
    print(f"  기록  {entry['date']} ({entry['kind']}) -> {entry['path']}")
    print(f"  URL   {entry['url']}")
    print(f"  {'갱신' if fm else '건너뜀'}  canonical_url  "
          f"{fm.relative_to(ROOT) if fm else '(포스팅 파일 없음)'}")
    print(f"  {'갱신' if nv else '건너뜀'}  네이버 링크    "
          f"{nv.relative_to(OUT_DIR) if nv else '(요약본 없음)'}")
    print()
    if nv:
        print("  네이버에 붙여넣기 전이면 갱신된 post.txt 를 쓸 것.")
        print("  이미 붙여넣었으면 링크 한 줄만 직접 고치면 된다.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
