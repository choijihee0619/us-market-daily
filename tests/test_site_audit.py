"""사이트 점검 로직 검증 (외부망 없음).

합성 HTML/XML 픽스처에 알려진 결함을 심고 되찾는 방식이다. 특히 티스토리 고유의
두 함정을 재현한다.
  - 정적자산 해시 소문자 'g-abc...' 를 GA4 측정 ID로 오탐하지 않는가
  - 모바일이 별도 스킨으로 갈려 태그가 빠진 상태를 잡아내는가
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.publish import site_audit as SA  # noqa: E402

SITE = "https://dailyresidualnote.com"
GA4 = "G-ABC1234567"

# 실제 티스토리 응답에서 확인한 요소를 축약해 옮겼다(2026-07-30 관측).
# 소문자 해시가 대량으로 섞여 있는 게 핵심이다 -- 대소문자 무시 정규식이면 전부 오탐된다.
TISTORY_NOISE = (
    '<link rel="stylesheet" href="//t1.daumcdn.net/tistory_admin/'
    'g-a4c0c7de8444349505c6390fbdadeac2f41c8d8d/style.css">\n'
    '<script src="//t1.daumcdn.net/g-abcdef1234567890/lib.js"></script>\n'
)


def _page(*, ga4: str | None = GA4, canonical: str = SITE,
          gverify: str | None = None, loader: bool = True) -> str:
    h = ["<html><head>", TISTORY_NOISE]
    if ga4:
        if loader:
            h.append(f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga4}">'
                     "</script>")
        h.append("<script>window.dataLayer=window.dataLayer||[];"
                 "function gtag(){dataLayer.push(arguments);}gtag('js',new Date());"
                 f"gtag('config','{ga4}');</script>")
    if canonical:
        h.append(f'<link rel="canonical" href="{canonical}"/>')
    if gverify:
        h.append(f'<meta name="google-site-verification" content="{gverify}"/>')
    h.append("</head><body>본문</body></html>")
    return "\n".join(h)


def _codes(findings, level=None):
    return [f.code for f in findings if level is None or f.level == level]


# ------------------------------------------------------------------ GA4

def test_ga4_detection():
    # 1) 정상
    fs = SA.check_ga4(_page(), GA4, "desktop")
    assert SA.FAIL not in [f.level for f in fs], _codes(fs, SA.FAIL)
    assert SA.find_ga4_ids(_page()) == [GA4]

    # 2) 티스토리 소문자 해시를 측정 ID로 오탐하지 않는다 (이 테스트의 존재 이유)
    only_noise = f"<html><head>{TISTORY_NOISE}</head></html>"
    assert SA.find_ga4_ids(only_noise) == [], "소문자 해시를 측정 ID로 오탐했다"
    fs = SA.check_ga4(only_noise, GA4, "desktop")
    assert "ga4_missing.desktop" in _codes(fs, SA.FAIL)

    # 3) ID 불일치 (스킨에 옛 속성 ID가 남은 경우)
    fs = SA.check_ga4(_page(ga4="G-OLD9999999"), GA4, "desktop")
    assert "ga4_id_mismatch.desktop" in _codes(fs, SA.FAIL)

    # 4) 로더 없이 config 호출만 남은 경우 (붙여넣기가 잘렸다)
    fs = SA.check_ga4(_page(loader=False), GA4, "desktop")
    assert "ga4_loader_missing.desktop" in _codes(fs, SA.FAIL)

    # 5) 태그 중복 = 이중 집계
    dup = _page().replace("</head>", f"<script>gtag('config','G-DUP1234567');</script></head>")
    fs = SA.check_ga4(dup, GA4, "desktop")
    assert "ga4_duplicate.desktop" in _codes(fs, SA.WARN)
    print("  GA4 5개 시나리오 통과 (오탐 방지 포함)")


# ------------------------------------------------------------ canonical

def test_canonical():
    fs = SA.check_canonical(_page(), SITE)
    assert SA.OK in [f.level for f in fs]

    # 개인 도메인 연결이 안 된 상태 -- SEO 신호가 플랫폼 주소에 쌓인다
    fs = SA.check_canonical(_page(canonical="https://heeppiness.tistory.com/3"), SITE)
    assert "canonical_host" in _codes(fs, SA.FAIL)

    fs = SA.check_canonical(_page(canonical=""), SITE)
    assert "canonical_missing" in _codes(fs, SA.FAIL)

    # 글 경로 불일치
    fs = SA.check_canonical(_page(canonical=f"{SITE}/3"), SITE, "/7")
    assert "canonical_path" in _codes(fs, SA.WARN)
    print("  canonical 4개 시나리오 통과")


def test_verification():
    # config에 값이 없으면 검사하지 않는다(DNS TXT로 확인한 경우가 정상)
    assert _codes(SA.check_verification(_page(), None, None), SA.FAIL) == []

    fs = SA.check_verification(_page(gverify="abc123"), "abc123", None)
    assert SA.OK in [f.level for f in fs]

    fs = SA.check_verification(_page(), "abc123", None)
    assert "verify_missing.google" in _codes(fs, SA.FAIL)

    fs = SA.check_verification(_page(gverify="zzz"), "abc123", None)
    assert "verify_mismatch.google" in _codes(fs, SA.FAIL)
    print("  소유확인 4개 시나리오 통과")


# -------------------------------------------------------- robots/sitemap

ROBOTS_TISTORY = (
    "User-agent: *\nDisallow: /guestbook\nDisallow: /manage\nDisallow: /search\n\n"
    "User-agent: bingbot\nCrawl-delay: 20\n"
)


def test_robots():
    fs = SA.check_robots(ROBOTS_TISTORY)
    assert _codes(fs, SA.FAIL) == []
    # 티스토리는 Sitemap 지시자를 넣지 않는다 -> 서치콘솔 수동 제출 필수
    assert "robots_no_sitemap" in _codes(fs, SA.WARN)

    fs = SA.check_robots("User-agent: *\nDisallow: /\n")
    assert "robots_blanket" in _codes(fs, SA.FAIL)

    # 다른 UA 블록의 Disallow: / 는 전체 차단이 아니다
    fs = SA.check_robots("User-agent: *\nDisallow: /manage\n\nUser-agent: BadBot\nDisallow: /\n")
    assert "robots_blanket" not in _codes(fs, SA.FAIL)
    print("  robots 3개 시나리오 통과")


def _sitemap(*locs: str) -> str:
    body = "".join(f"<url><loc>{u}</loc></url>" for u in locs)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{body}</urlset>")


def test_sitemap():
    # 발행 0편 상태 (실제 관측: 홈 + 카테고리만)
    fs = SA.check_sitemap(_sitemap(SITE, f"{SITE}/category"), SITE)
    assert _codes(fs, SA.FAIL) == []
    assert "sitemap_no_posts" in _codes(fs, SA.INFO)

    # 글이 쌓인 상태
    fs = SA.check_sitemap(_sitemap(SITE, f"{SITE}/3", f"{SITE}/4"), SITE)
    assert "sitemap_posts" in _codes(fs, SA.OK)

    # 개인 도메인이 아닌 주소가 섞였다
    fs = SA.check_sitemap(_sitemap(SITE, "https://heeppiness.tistory.com/3"), SITE)
    assert "sitemap_host" in _codes(fs, SA.FAIL)

    assert "sitemap_empty" in _codes(SA.check_sitemap("<urlset/>", SITE), SA.FAIL)
    print("  sitemap 4개 시나리오 통과")


# ------------------------------------------------- 전체 점검 (fetch 주입)

def test_audit_mobile_gap():
    """가장 중요한 시나리오. 데스크톱 스킨에는 태그를 넣었고 /m/ 에는 안 들어간 상태.

    이 상태로 방치하면 모바일 트래픽이 통째로 누락되고, GA4는 소급 수집이 없어
    그 기간은 영구히 복구되지 않는다.
    """
    desktop = _page()
    mobile = f"<html><head>{TISTORY_NOISE}"'<link rel="canonical" href="'f'{SITE}"/></head></html>'

    def fake_fetch(url, ua=SA.UA_DESKTOP, timeout=20):
        if url.endswith("/robots.txt"):
            return 200, ROBOTS_TISTORY
        if url.endswith("/sitemap.xml"):
            return 200, _sitemap(SITE, f"{SITE}/category")
        if url.endswith("/rss"):
            return 200, "<rss><channel><title>t</title></channel></rss>"
        return 200, (mobile if ua == SA.UA_MOBILE else desktop)

    fs = SA.audit(SITE, ga4_id=GA4, fetch=fake_fetch)
    codes = _codes(fs)
    assert "ga4.desktop" in codes, codes
    assert "ga4_missing.mobile" in _codes(fs, SA.FAIL), codes
    assert "mobile_skin_split" in _codes(fs, SA.WARN), codes
    print(f"  모바일 누락 탐지 OK (FAIL {SA.summarize(fs)['fail']}건)")

    # 양쪽 다 정상이면 FAIL 0
    def good_fetch(url, ua=SA.UA_DESKTOP, timeout=20):
        s, t = fake_fetch(url, ua, timeout)
        return s, (desktop if t == mobile else t)

    fs = SA.audit(SITE, ga4_id=GA4, fetch=good_fetch)
    assert SA.summarize(fs)["fail"] == 0, _codes(fs, SA.FAIL)
    print("  정상 상태에서 FAIL 0 확인")


def test_snippet():
    s = SA.ga4_snippet(GA4)
    assert s.count(GA4) == 2 and "gtag/js" in s
    # 생성한 스니펫을 그대로 점검기에 통과시킬 수 있어야 한다 (왕복 검증)
    page = f"<html><head>{s}<link rel='canonical' href='{SITE}'/></head></html>"
    assert _codes(SA.check_ga4(page, GA4, "desktop"), SA.FAIL) == []
    assert SA.ga4_snippet("") .count("G-XXXXXXXXXX") == 2
    print("  스니펫 생성 -> 점검 왕복 통과")


if __name__ == "__main__":
    print("\n[1] GA4 태그 탐지")
    test_ga4_detection()
    print("\n[2] canonical")
    test_canonical()
    print("\n[3] 소유확인 메타")
    test_verification()
    print("\n[4] robots.txt")
    test_robots()
    print("\n[5] sitemap.xml")
    test_sitemap()
    print("\n[6] 전체 점검 - 모바일 스킨 누락 (핵심)")
    test_audit_mobile_gap()
    print("\n[7] 스니펫 왕복")
    test_snippet()
    print("\n전체 통과")
