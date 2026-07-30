#!/usr/bin/env python3
"""배포된 사이트 점검 — GA4 태그 / canonical / 서치콘솔 / sitemap.

손으로 한 설정을 객관적으로 확인하는 용도다. GA4는 소급 수집이 없으므로
태그가 빠진 것을 늦게 알면 그 기간 트래픽은 영구히 사라진다.

사용:
    python scripts/check_site.py                  # 전체 점검
    python scripts/check_site.py --snippet        # 스킨에 붙일 코드 출력
    python scripts/check_site.py --post-url https://dailyresidualnote.com/3
    python scripts/check_site.py --latest-post    # 레지스트리의 최신 글로 점검

설정은 config.yaml 의 site 블록을 읽는다(측정 ID는 페이지 소스에 노출되는
공개 값이라 .env가 아니라 config.yaml에 둔다).

종료코드: FAIL이 하나라도 있으면 1. WARN만 있으면 0.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402
from src.publish import site_audit as SA  # noqa: E402
from src.publish import url_registry as REG  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
ROOT = Path(__file__).resolve().parents[1]


def _latest_post_url() -> str | None:
    reg = REG.load(ROOT)
    if not reg:
        return None
    newest = max(reg.values(), key=lambda e: (e.get("date", ""), e.get("kind", "")))
    return newest.get("url")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--post-url", default=None, help="개별 글 URL도 함께 점검")
    ap.add_argument("--latest-post", action="store_true",
                    help="data/post_urls.json 의 최신 글을 점검 대상에 추가")
    ap.add_argument("--no-mobile", action="store_true", help="모바일 UA 점검 생략")
    ap.add_argument("--snippet", action="store_true",
                    help="스킨 </head> 앞에 붙일 코드를 출력하고 종료")
    args = ap.parse_args()

    cfg = load_config(args.config)
    site_url = cfg.get_path("report.site_url", "").rstrip("/")
    ga4_id = cfg.get_path("site.ga4_measurement_id", "") or ""
    g_verify = cfg.get_path("site.google_site_verification", "") or ""
    n_verify = cfg.get_path("site.naver_site_verification", "") or ""

    if not site_url:
        print("config.yaml 의 report.site_url 이 비어 있다.")
        return 1

    if args.snippet:
        print("# 티스토리: 관리 → 스킨 편집 → html 편집 → </head> 바로 앞에 붙여넣고 적용")
        print("# 설치 절차 전체: docs/SETUP_ANALYTICS.md")
        print()
        if not ga4_id:
            print("# (config.yaml 의 site.ga4_measurement_id 가 비어 있어 자리표시자로 출력한다)")
        print(SA.ga4_snippet(ga4_id))
        vs = SA.verification_snippet(g_verify, n_verify)
        if vs:
            print()
            print("<!-- 소유확인 (DNS TXT로 확인했다면 불필요) -->")
            print(vs)
        return 0

    post_url = args.post_url
    if args.latest_post and not post_url:
        post_url = _latest_post_url()
        if not post_url:
            print("레지스트리가 비어 있다(발행 0편). 개별 글 점검을 건너뛴다.\n")

    findings = SA.audit(site_url, ga4_id=ga4_id, google_verify=g_verify,
                        naver_verify=n_verify, post_url=post_url,
                        check_mobile=not args.no_mobile)

    print(f"점검 대상: {site_url}")
    print("-" * 78)
    for f in findings:
        print(f)
    print("-" * 78)
    c = SA.summarize(findings)
    print(f"FAIL {c['fail']} · WARN {c['warn']} · OK {c['ok']} · INFO {c['info']}")

    if c["fail"]:
        print("\n조치 방법: docs/SETUP_ANALYTICS.md")
    return 1 if c["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
