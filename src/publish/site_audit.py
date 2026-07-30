"""배포된 사이트 점검 (읽기 전용).

왜 필요한가:
GA4 태그 삽입과 서치콘솔 소유확인은 티스토리 관리자 화면에서 손으로 하는 작업이다
(Open API가 2024-02 종료되어 다른 경로가 없다). 손으로 한 설정은 "했다고 생각했는데
안 된" 상태로 조용히 남는다. 그리고 **GA4는 소급 수집이 없다** -- 태그가 빠진 기간의
트래픽은 영구히 소실된다. 그래서 실제 배포된 HTML을 받아서 확인하는 단계를 둔다.

티스토리 고유의 함정 두 개를 겨냥한다.

1. **모바일 별도 스킨.** 티스토리는 모바일 UA 요청을 `/m/` 의 시스템 스킨으로 보낸다.
   데스크톱 스킨 `</head>` 에 넣은 gtag는 그 페이지에서 실행되지 않는다. 국내 블로그
   트래픽은 모바일 비중이 크므로 이걸 놓치면 수치가 조용히 반토막 난다.
   그래서 모바일 UA로 한 번 더 받아본다.
2. **두 호스트 병존.** `heeppiness.tistory.com` 이 계속 살아 있고 티스토리는 개인
   도메인으로 리다이렉트하지 않는다. canonical과 sitemap의 `<loc>` 가 개인 도메인을
   가리키는지 확인해야 SEO 신호가 한쪽으로 모인다.

판정 로직(순수 함수)과 네트워크 계층을 분리했다. 판정은 외부망 없이
tests/test_site_audit.py 가 합성 픽스처로 검증한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# 데스크톱/모바일을 가르는 UA. 티스토리는 UA로 스킨을 갈라 보내므로
# 이 두 개로 각각 받아봐야 실제 사용자가 보는 HTML을 확인할 수 있다.
UA_DESKTOP = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# 측정 ID는 반드시 대문자다(G-XXXXXXXXXX). 티스토리 정적자산 해시에 소문자 'g-...'가
# 대량으로 등장하므로 대소문자를 구분하지 않으면 전부 오탐이 된다.
RE_GA4_ID = re.compile(r"\bG-[A-Z0-9]{6,15}\b")
RE_GTAG_SRC = re.compile(r"googletagmanager\.com/gtag/js", re.I)
RE_GTM_SRC = re.compile(r"googletagmanager\.com/gtm\.js", re.I)
RE_GTAG_CONFIG = re.compile(r"gtag\s*\(\s*['\"]config['\"]")
RE_CANONICAL = re.compile(r"""<link[^>]+rel=["']canonical["'][^>]*>""", re.I)
RE_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
RE_CONTENT = re.compile(r"""content=["']([^"']*)["']""", re.I)
RE_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
RE_RSS_ITEM = re.compile(r"<item[\s>]", re.I)

FAIL, WARN, OK, INFO = "fail", "warn", "ok", "info"


@dataclass
class Finding:
    level: str          # fail | warn | ok | info
    code: str
    message: str

    def __str__(self) -> str:
        mark = {FAIL: "FAIL", WARN: "WARN", OK: " OK ", INFO: "INFO"}[self.level]
        return f"[{mark}] {self.code:<22} {self.message}"


def _host(url: str) -> str:
    h = urlsplit(str(url).strip()).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _meta_content(html: str, name: str) -> str | None:
    """<meta name="..." content="..."> 의 content. 속성 순서가 뒤바뀐 경우도 잡는다."""
    for m in re.finditer(r"<meta[^>]*>", html, re.I):
        tag = m.group(0)
        nm = re.search(r"""(?:name|property)=["']([^"']+)["']""", tag, re.I)
        if nm and nm.group(1).strip().lower() == name.lower():
            cm = RE_CONTENT.search(tag)
            return cm.group(1).strip() if cm else ""
    return None


# --------------------------------------------------------------- GA4 태그

def find_ga4_ids(html: str) -> list[str]:
    """페이지에 박힌 GA4 측정 ID 목록(중복 제거, 등장 순서 유지)."""
    seen: list[str] = []
    for mid in RE_GA4_ID.findall(html or ""):
        if mid not in seen:
            seen.append(mid)
    return seen


def check_ga4(html: str, expected: str | None = None, where: str = "desktop") -> list[Finding]:
    html = html or ""
    ids = find_ga4_ids(html)
    has_loader = bool(RE_GTAG_SRC.search(html) or RE_GTM_SRC.search(html))
    out: list[Finding] = []

    if not ids and not has_loader:
        out.append(Finding(FAIL, f"ga4_missing.{where}",
                           f"{where} 페이지에 GA4 태그가 없다. 이 기간 트래픽은 소급 복구되지 않는다."))
        return out

    if not has_loader:
        out.append(Finding(FAIL, f"ga4_loader_missing.{where}",
                           f"측정 ID({', '.join(ids)})는 있는데 gtag.js 로더가 없다. 수집되지 않는다."))
    elif not ids and RE_GTM_SRC.search(html):
        # GTM 컨테이너 경유면 측정 ID가 페이지 소스에 안 보이는 게 정상이다.
        out.append(Finding(INFO, f"ga4_via_gtm.{where}",
                           "GTM 컨테이너 경유로 보인다. 측정 ID는 GTM 내부에 있어 여기서 확인할 수 없다."))
    elif not RE_GTAG_CONFIG.search(html) and not RE_GTM_SRC.search(html):
        out.append(Finding(WARN, f"ga4_config_missing.{where}",
                           "gtag.js는 있으나 gtag('config', ...) 호출이 없다. 스니펫이 잘렸는지 확인할 것."))

    if expected:
        expected = expected.strip()
        if ids and expected not in ids:
            out.append(Finding(FAIL, f"ga4_id_mismatch.{where}",
                               f"설정값 {expected} 과 페이지의 {', '.join(ids)} 가 다르다."))
        elif ids:
            out.append(Finding(OK, f"ga4.{where}", f"측정 ID {expected} 확인."))
    elif ids:
        out.append(Finding(WARN, f"ga4_id_unconfigured.{where}",
                           f"페이지에서 {', '.join(ids)} 를 찾았으나 config.yaml 의 "
                           f"site.ga4_measurement_id 가 비어 있다. 기입하면 이후 변경을 감지한다."))

    if len(ids) > 1:
        out.append(Finding(WARN, f"ga4_duplicate.{where}",
                           f"측정 ID가 {len(ids)}개다({', '.join(ids)}). 이중 집계로 조회수가 부풀 수 있다."))
    return out


# ------------------------------------------------------------- canonical

def check_canonical(html: str, site_url: str, expect_path: str | None = None) -> list[Finding]:
    """canonical이 수익원(개인 도메인)을 가리키는지 본다.

    티스토리는 기본 주소를 살려두고 리다이렉트하지 않으므로, canonical이 tistory.com
    을 가리키면 SEO 신호가 플랫폼 종속 주소에 쌓인다.
    """
    m = RE_CANONICAL.search(html or "")
    if not m:
        return [Finding(FAIL, "canonical_missing", "canonical 태그가 없다.")]
    href = RE_HREF.search(m.group(0))
    if not href:
        return [Finding(FAIL, "canonical_empty", "canonical 태그에 href가 없다.")]

    url = href.group(1).strip()
    want, got = _host(site_url), _host(url)
    out: list[Finding] = []
    if not got:
        out.append(Finding(WARN, "canonical_relative", f"canonical이 상대경로다: {url}"))
    elif got != want:
        out.append(Finding(FAIL, "canonical_host",
                           f"canonical이 {got} 를 가리킨다. {want} 여야 한다."))
    else:
        out.append(Finding(OK, "canonical", f"{url}"))

    if expect_path:
        got_path = urlsplit(url).path.rstrip("/") or "/"
        want_path = expect_path.rstrip("/") or "/"
        if got_path != want_path:
            out.append(Finding(WARN, "canonical_path",
                               f"canonical 경로 {got_path} 가 요청 경로 {want_path} 와 다르다."))
    return out


# ---------------------------------------------------------- 소유확인 메타

def check_verification(html: str, google: str | None = None,
                       naver: str | None = None) -> list[Finding]:
    """서치콘솔·서치어드바이저 소유확인 메타태그.

    DNS TXT로 확인했다면 메타태그는 없어도 정상이므로, config에 값을 넣은 경우에만
    검사한다. 값을 넣었는데 페이지에 없으면 스킨 저장이 안 된 것이다.
    """
    out: list[Finding] = []
    for label, cfg_val, meta_name in (
        ("google", google, "google-site-verification"),
        ("naver", naver, "naver-site-verification"),
    ):
        found = _meta_content(html or "", meta_name)
        if not cfg_val:
            if found:
                out.append(Finding(INFO, f"verify_found.{label}",
                                   f"{meta_name} 메타가 있다({found[:12]}...). config에도 적어두면 검사한다."))
            continue
        if found is None:
            out.append(Finding(FAIL, f"verify_missing.{label}",
                               f"config에 {label} 소유확인 값이 있는데 페이지에 {meta_name} 메타가 없다."))
        elif found.strip() != cfg_val.strip():
            out.append(Finding(FAIL, f"verify_mismatch.{label}",
                               f"{meta_name} 값이 config와 다르다."))
        else:
            out.append(Finding(OK, f"verify.{label}", f"{meta_name} 확인."))
    return out


# ------------------------------------------------------------- robots.txt

def check_robots(text: str) -> list[Finding]:
    """티스토리 robots.txt는 편집할 수 없다. 그래서 여기서는 '무엇을 포기해야 하는가'를
    확인하는 용도다. Sitemap 지시자가 없으면 서치콘솔에 수동 제출이 필수다.
    """
    text = text or ""
    out: list[Finding] = []
    if not text.strip():
        return [Finding(WARN, "robots_empty", "robots.txt가 비어 있거나 받지 못했다.")]

    # 'User-agent: *' 블록에서 전체 차단이 걸렸는지만 본다.
    blanket = False
    ua_all = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        k, _, v = s.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            ua_all = v == "*"
        elif k == "disallow" and ua_all and v == "/":
            blanket = True
    if blanket:
        out.append(Finding(FAIL, "robots_blanket", "robots.txt가 전체 경로를 차단한다."))
    else:
        out.append(Finding(OK, "robots", "전체 차단 없음."))

    if not re.search(r"^\s*sitemap\s*:", text, re.I | re.M):
        out.append(Finding(WARN, "robots_no_sitemap",
                           "robots.txt에 Sitemap 지시자가 없다(티스토리는 편집 불가). "
                           "서치콘솔에 sitemap.xml을 직접 제출해야 한다."))
    return out


# --------------------------------------------------------------- sitemap

def check_sitemap(xml: str, site_url: str) -> list[Finding]:
    locs = RE_LOC.findall(xml or "")
    if not locs:
        return [Finding(FAIL, "sitemap_empty", "sitemap.xml에서 <loc>를 찾지 못했다.")]

    want = _host(site_url)
    bad = sorted({_host(u) for u in locs if _host(u) != want})
    out: list[Finding] = []
    if bad:
        out.append(Finding(FAIL, "sitemap_host",
                           f"sitemap의 <loc> 호스트가 {', '.join(bad)} 다. {want} 여야 한다."))
    else:
        out.append(Finding(OK, "sitemap", f"<loc> {len(locs)}건, 전부 {want}."))

    # 티스토리 sitemap은 글이 0편이어도 홈·카테고리·태그 경로를 넣어준다.
    # 그래서 '건수 > 0'이 아니라 '글로 보이는 경로가 있는가'를 봐야 한다.
    posts = [u for u in locs
             if re.match(r"^/\d+$", urlsplit(u).path.rstrip("/") or "/")]
    if not posts:
        out.append(Finding(INFO, "sitemap_no_posts",
                           "글 경로(/숫자)가 아직 없다. 첫 발행 후 다시 확인할 것."))
    else:
        out.append(Finding(OK, "sitemap_posts", f"글 경로 {len(posts)}건."))
    return out


def check_rss(xml: str) -> list[Finding]:
    n = len(RE_RSS_ITEM.findall(xml or ""))
    if not (xml or "").strip():
        return [Finding(WARN, "rss_missing", "RSS를 받지 못했다.")]
    if n == 0:
        return [Finding(INFO, "rss_empty", "RSS에 <item>이 없다(발행 0편이면 정상).")]
    return [Finding(OK, "rss", f"<item> {n}건.")]


# ------------------------------------------------------- 네트워크 계층

def default_fetch(url: str, ua: str = UA_DESKTOP, timeout: int = 20) -> tuple[int, str]:
    """(status_code, text). 실패는 예외 대신 (0, "")로 돌려 점검이 끝까지 진행되게 한다."""
    try:
        import requests
    except ImportError:                                     # pragma: no cover
        log.error("requests 미설치: pip install requests")
        return 0, ""
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout,
                         allow_redirects=True)
        return r.status_code, r.text
    except Exception as e:
        log.warning("요청 실패 %s: %s", url, e)
        return 0, ""


def audit(site_url: str, *, ga4_id: str | None = None,
          google_verify: str | None = None, naver_verify: str | None = None,
          post_url: str | None = None, check_mobile: bool = True,
          fetch=default_fetch) -> list[Finding]:
    """사이트 전체 점검. fetch를 주입하면 외부망 없이 테스트할 수 있다."""
    site_url = site_url.rstrip("/")
    out: list[Finding] = []

    status, html = fetch(site_url + "/", UA_DESKTOP)
    if status != 200 or not html:
        return [Finding(FAIL, "home_unreachable", f"{site_url} 응답 {status}. 이후 점검을 건너뛴다.")]
    out.append(Finding(OK, "home", f"{site_url} 200 ({len(html):,} bytes)"))

    out += check_ga4(html, ga4_id, "desktop")
    out += check_canonical(html, site_url)
    out += check_verification(html, google_verify, naver_verify)

    if check_mobile:
        m_status, m_html = fetch(site_url + "/", UA_MOBILE)
        if m_status == 200 and m_html:
            # 모바일이 별도 스킨으로 갈렸는지의 대리지표: 본문 길이가 크게 다르다.
            out += check_ga4(m_html, ga4_id, "mobile")
            if find_ga4_ids(m_html) != find_ga4_ids(html):
                out.append(Finding(WARN, "mobile_skin_split",
                                   "모바일 페이지의 태그 상태가 데스크톱과 다르다. 티스토리 "
                                   "'모바일 웹 자동 연결'이 켜져 있으면 /m/ 시스템 스킨이 나가고 "
                                   "스킨 편집이 적용되지 않는다."))
        else:
            out.append(Finding(WARN, "mobile_unreachable", f"모바일 UA 응답 {m_status}."))

    r_status, robots = fetch(site_url + "/robots.txt", UA_DESKTOP)
    out += check_robots(robots) if r_status == 200 else [
        Finding(WARN, "robots_unreachable", f"robots.txt 응답 {r_status}.")]

    s_status, sitemap = fetch(site_url + "/sitemap.xml", UA_DESKTOP)
    out += check_sitemap(sitemap, site_url) if s_status == 200 else [
        Finding(FAIL, "sitemap_unreachable", f"sitemap.xml 응답 {s_status}.")]

    f_status, rss = fetch(site_url + "/rss", UA_DESKTOP)
    out += check_rss(rss) if f_status == 200 else [
        Finding(WARN, "rss_unreachable", f"rss 응답 {f_status}.")]

    if post_url:
        p_status, p_html = fetch(post_url, UA_DESKTOP)
        if p_status == 200 and p_html:
            out.append(Finding(OK, "post", f"{post_url} 200"))
            out += check_ga4(p_html, ga4_id, "post")
            out += check_canonical(p_html, site_url, urlsplit(post_url).path)
        else:
            out.append(Finding(WARN, "post_unreachable", f"{post_url} 응답 {p_status}."))
    return out


def summarize(findings: list[Finding]) -> dict[str, int]:
    c = {FAIL: 0, WARN: 0, OK: 0, INFO: 0}
    for f in findings:
        c[f.level] = c.get(f.level, 0) + 1
    return c


# -------------------------------------------------------------- 스니펫 생성

def ga4_snippet(measurement_id: str) -> str:
    """티스토리 스킨 </head> 앞에 붙일 GA4 기본 스니펫."""
    mid = (measurement_id or "G-XXXXXXXXXX").strip()
    return (
        "<!-- Google tag (gtag.js) -->\n"
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>\n'
        "<script>\n"
        "  window.dataLayer = window.dataLayer || [];\n"
        "  function gtag(){dataLayer.push(arguments);}\n"
        "  gtag('js', new Date());\n"
        f"  gtag('config', '{mid}');\n"
        "</script>"
    )


def verification_snippet(google: str | None = None, naver: str | None = None) -> str:
    lines = []
    if google:
        lines.append(f'<meta name="google-site-verification" content="{google.strip()}" />')
    if naver:
        lines.append(f'<meta name="naver-site-verification" content="{naver.strip()}" />')
    return "\n".join(lines)
