"""숫자형 포스트 주소 대응 검증.

티스토리 포스트 주소가 '숫자'(/123)일 때 애널리틱스가 여전히 글을 식별하는지,
그리고 발행 후 링크 기록이 canonical과 네이버 요약본에 소급 반영되는지 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.publish import url_registry as REG  # noqa: E402
from src.report import analytics_report as AR  # noqa: E402

TMP = Path(__file__).resolve().parents[1] / "out" / "_verify3"
SITE = "https://dailyresidualnote.com"


def setup():
    repo = TMP / "repo"
    (repo / "posts").mkdir(parents=True, exist_ok=True)
    (TMP / "out" / "2026-07-28" / "naver").mkdir(parents=True, exist_ok=True)

    # 도메인 미설정 상태로 생성된 것처럼 canonical 없이 만든다
    (repo / "posts" / "2026-07-28.md").write_text(
        "---\n"
        'title: "2026-07-28 | 10Y -6bp"\n'
        "date: 2026-07-28\n"
        "layout: post\n"
        "spread_bp: 27.60\n"
        "---\n\n"
        "# 본문\n", encoding="utf-8")

    (TMP / "out" / "2026-07-28" / "naver" / "post.txt").write_text(
        "2026-07-28 | 10Y -6bp\n\n"
        "[ 주요 지수 ]\nS&P 500   +0.43%\n\n"
        "──────────────────────\n\n"
        "종목별 잔차는 전체 리포트에 있습니다.\n\n"
        "본 글은 공개 데이터를 자동 수집·분석한 연구 기록이며 투자자문이 아닙니다.\n",
        encoding="utf-8")
    return repo


def test_url_parsing():
    assert REG.url_path("https://dailyresidualnote.com/123") == "/123"
    assert REG.url_path("https://dailyresidualnote.com/123/") == "/123"
    assert REG.url_path("https://dailyresidualnote.com/123?utm=x") == "/123"
    assert REG.url_path("https://dailyresidualnote.com/2026-07-28") == "/2026-07-28"
    print("  URL -> 경로 정규화 OK (쿼리·슬래시 제거)")


def test_record_and_patch(repo):
    url = f"{SITE}/123"
    entry = REG.record(repo, "2026-07-28", url, "daily")
    assert entry["path"] == "/123"
    assert REG.get(repo, "2026-07-28", "daily")["url"] == url
    print(f"  기록: 2026-07-28 -> {entry['path']}")

    # canonical 없던 front matter에 삽입되는지
    fm = REG.patch_front_matter(repo, "2026-07-28", url, "daily")
    assert fm is not None
    text = fm.read_text(encoding="utf-8")
    assert f"canonical_url: {url}" in text
    assert text.count("canonical_url:") == 1, "중복 삽입"
    assert text.startswith("---\n"), "front matter 구조 파손"
    assert "# 본문" in text, "본문 유실"
    print("  canonical_url 삽입 OK (없던 필드를 추가)")

    # 재실행 시 중복 없이 교체되는지 (멱등성)
    url2 = f"{SITE}/124"
    REG.patch_front_matter(repo, "2026-07-28", url2, "daily")
    text = fm.read_text(encoding="utf-8")
    assert text.count("canonical_url:") == 1
    assert url2 in text and url not in text
    print("  재실행 시 교체 OK (멱등)")

    # 네이버 요약본에 링크 줄이 없던 경우 -> 면책 문구 앞에 삽입
    nv = REG.patch_naver(TMP / "out", "2026-07-28", url2, "daily")
    assert nv is not None
    t = nv.read_text(encoding="utf-8")
    assert f"전체 리포트 → {url2}" in t
    assert t.count("http") == 1, f"링크가 1개가 아님: {t.count('http')}"
    idx_link = t.index("전체 리포트")
    idx_disc = t.index("투자자문이 아닙니다")
    assert idx_link < idx_disc, "링크가 면책 문구 뒤에 들어감"
    print("  네이버 링크 삽입 OK (면책 문구 앞, 외부 링크 1개 유지)")

    # 다시 갱신해도 링크가 늘지 않는지
    REG.patch_naver(TMP / "out", "2026-07-28", f"{SITE}/125", "daily")
    t = nv.read_text(encoding="utf-8")
    assert t.count("http") == 1, "재갱신 시 링크 중복"
    assert f"{SITE}/125" in t
    print("  네이버 링크 재갱신 OK (멱등)")


def test_analytics_with_numeric_paths(repo):
    """핵심 회귀 테스트: /123 형태로도 성과 분석이 되는지."""
    # 숫자 경로 3편 등록
    mapping = {"2026-07-24": "/101", "2026-07-27": "/102", "2026-07-28": "/103"}
    for d, p in mapping.items():
        REG.record(repo, d, f"{SITE}{p}", "daily")
    REG.record(repo, "2026-07-20", f"{SITE}/200", "weekly")

    registry = REG.path_to_post(repo)
    assert len(registry) == 4
    print(f"  역인덱스 {len(registry)}건: {sorted(registry)}")

    # 매핑 없이는 실패해야 정상 (문제가 실재함을 확인)
    assert AR.classify_path("/103") == (None, None)
    # 매핑이 있으면 성공
    kind, date = AR.classify_path("/103", registry)
    assert kind == "daily" and date == pd.Timestamp("2026-07-28")
    print("  /103 -> (daily, 2026-07-28)  매핑 참조 성공")

    kind, date = AR.classify_path("/200", registry)
    assert kind == "weekly"
    print("  /200 -> (weekly, 2026-07-20)")

    # 날짜형 경로 하위호환
    assert AR.classify_path("/2026-07-24")[0] == "daily"
    assert AR.classify_path("/2026-07-20_weekly")[0] == "weekly"
    print("  날짜형 경로 하위호환 유지")

    # 매칭 안 되는 경로는 걸러진다
    assert AR.classify_path("/about", registry) == (None, None)

    # 실제 패널 구축
    rows = []
    for d, p in mapping.items():
        for k in range(4):
            rows.append({"date": pd.Timestamp(d) + pd.Timedelta(days=k), "path": p,
                         "views": 40 + k, "users": 20, "avg_duration_s": 95.0})
    for k in range(12):
        rows.append({"date": pd.Timestamp("2026-07-20") + pd.Timedelta(days=k),
                     "path": "/200", "views": 70, "users": 40, "avg_duration_s": 180.0})
    rows += [{"date": pd.Timestamp("2026-07-28"), "path": "/about",
              "views": 5, "users": 5, "avg_duration_s": 10.0}]
    traffic = pd.DataFrame(rows)

    earnings = pd.DataFrame([{"date": pd.Timestamp(d), "path": p, "earnings": 0.3,
                              "impressions": 120, "clicks": 1, "rpm": 2.5}
                             for d, p in mapping.items()])

    panel = AR.build_post_panel(traffic, earnings, pd.DataFrame(),
                                pd.DataFrame(), registry)
    assert not panel.empty, "숫자 경로 패널 구축 실패"
    assert len(panel) == 4, f"글 수 불일치: {len(panel)}"
    assert "/about" not in set(panel["path"]), "비글 경로가 섞임"
    print(f"  패널 {len(panel)}편 (about 제외됨)")

    res = AR.analyze(panel)
    assert res["available"]
    ratio = res.get("weekly_vs_daily_ratio")
    assert ratio is not None
    print(f"  주간/일간 비율 {ratio:.2f} -- 숫자 주소에서도 Q3 분석 동작")

    md = AR.build_markdown(res, 30)
    assert "/103" in md or "/200" in md
    print(f"  리포트 생성 OK ({len(md)}자)")


if __name__ == "__main__":
    repo = setup()
    print("\n[1] URL 파싱")
    test_url_parsing()
    print("\n[2] 기록 및 소급 갱신")
    test_record_and_patch(repo)
    print("\n[3] 숫자 경로 애널리틱스 (핵심)")
    test_analytics_with_numeric_paths(repo)
    print("\n전체 통과")
