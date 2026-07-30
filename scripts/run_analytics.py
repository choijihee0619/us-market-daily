#!/usr/bin/env python3
"""블로그 성과 리포트 (운영용, 발행하지 않음).

사용:
    python scripts/run_analytics.py
    python scripts/run_analytics.py --days 90

키가 없으면 data/analytics/{traffic,earnings}.csv 폴백으로 동작한다.
GA4·AdSense 웹 UI에서 CSV를 내려받아 넣으면 된다.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.collect import analytics as AN  # noqa: E402
from src.config import OUT_DIR, load_config  # noqa: E402
from src.process.weekly_stats import load_scorecard  # noqa: E402
from src.publish import url_registry as REG  # noqa: E402
from src.report import analytics_report as AR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("run_analytics")
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    load_config(args.config)

    traffic, earnings = AN.collect_all(days=args.days)
    if traffic.empty:
        log.error(
            "트래픽 데이터가 없다.\n"
            "  방법 1) GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS 설정\n"
            "  방법 2) GA4 웹 UI에서 CSV 내려받아 data/analytics/traffic.csv 로 저장\n"
            "         (필요 컬럼: date, page path, views, users, averageSessionDuration)"
        )
        return 1

    registry = REG.path_to_post(ROOT)
    log.info("URL 매핑 %d건 로드", len(registry))

    panel = AR.build_post_panel(traffic, earnings, load_scorecard(ROOT),
                                storage.read("prices"), registry)
    if panel.empty:
        log.error(
            "경로를 글로 매칭하지 못했다.\n"
            "  티스토리 포스트 주소가 '숫자'라면 발행 후마다 다음을 돌려야 한다:\n"
            "    python scripts/link_post.py https://도메인/글번호\n"
            "  기록 현황 확인: python scripts/link_post.py --list"
        )
        return 1

    res = AR.analyze(panel)
    md = AR.build_markdown(res, args.days)

    outdir = OUT_DIR / "_analytics"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.md").write_text(md, encoding="utf-8")
    panel.to_csv(outdir / "post_panel.csv", index=False)

    print()
    print(md)
    print()
    log.info("저장: %s", outdir / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
