"""주간 회고 · 애널리틱스 · Alpha Vantage 파서 검증 (외부망 없이)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collect import news_alphavantage as AV  # noqa: E402
from src.config import load_config  # noqa: E402
from src.process import weekly_stats as W  # noqa: E402
from src.report import analytics_report as AR  # noqa: E402
from src.report import weekly_builder as WB  # noqa: E402
from src.report import weekly_charts as WC  # noqa: E402

rng = np.random.default_rng(7)
ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "out" / "_verify2"


def sd_se(x) -> float:
    return float(np.std(x, ddof=1) / np.sqrt(len(x)))


def make_scorecard(n_days=60, true_mean_bp=5.0, sd=28.0):
    """알려진 평균을 심는다. Newey-West t가 그걸 되찾아야 한다."""
    dates = pd.bdate_range("2026-05-04", periods=n_days)
    spread = rng.normal(true_mean_bp, sd, n_days)
    rows = [{"date": d.strftime("%Y-%m-%d"), "spread_bp": round(float(s), 2),
             "top_bp": round(float(s / 2), 2), "bottom_bp": round(float(-s / 2), 2),
             "hit": bool(s > 0), "n": 180, "rev": "abc1234"}
            for d, s in zip(dates, spread)]
    d = TMP / "repo" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scorecard.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return TMP / "repo", dates, spread


def test_weekly_stats():
    repo, dates, spread = make_scorecard()
    sc = W.load_scorecard(repo)
    assert len(sc) == 60

    start, end = W.week_bounds(dates[-1])
    assert start.weekday() == 0 and end.weekday() == 4
    print(f"  주 경계: {start.date()}(월) ~ {end.date()}(금)")

    st = W.summarize(sc, start, end)
    assert st["available"]
    assert st["n_total"] == 60

    # Newey-West t와 단순 t를 비교. 자기상관이 없는 합성이라 비슷해야 정상.
    naive_t = spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))
    print(f"  일평균 {st['cum_mean_bp']:+.2f}bp "
          f"(모집단 5.00, 표본 {spread.mean():+.2f}, SE≈{sd_se(spread):.2f})")
    print(f"  Newey-West t = {st['cum_t']:.2f} / 단순 t = {naive_t:.2f}")
    # JSON에 소수 2자리로 반올림해 저장하므로 완전 일치는 아니다
    assert abs(st["cum_mean_bp"] - spread.mean()) < 0.01
    assert abs(st["cum_t"] - naive_t) < 1.0, "HAC t가 단순 t와 과도하게 괴리"
    assert 0 <= st["cum_binom_p"] <= 1
    print(f"  적중률 {st['cum_hit_rate']*100:.1f}%, 이항검정 p={st['cum_binom_p']:.3f}")
    print(f"  허들 통과({W.T_HURDLE}): {st['passes_hurdle']}")

    label, reason = W.verdict(st)
    print(f"  판정: {label}")
    assert label in ("판단 보류", "귀무가설 기각 실패", "결정적이지 않음",
                     "기각 실패 (신호 존재 가능)")
    return repo, sc, st, start, end


def test_verdict_boundaries():
    """판정 로직이 표본 크기와 t에 제대로 반응하는지."""
    assert W.verdict({"n_total": 5, "cum_t": 9.0})[0] == "판단 보류"
    assert W.verdict({"n_total": 60, "cum_t": 0.4})[0] == "귀무가설 기각 실패"
    assert W.verdict({"n_total": 60, "cum_t": 2.4})[0] == "결정적이지 않음"
    assert W.verdict({"n_total": 60, "cum_t": 3.9})[0].startswith("기각 실패")
    print("  4개 구간 모두 기대대로 분기")


def test_weekly_report(repo, st, start, end):
    cfg = load_config()
    charts = WC.build_all(st, TMP / "images")
    assert len(charts) >= 2, f"주간 차트 부족: {len(charts)}"
    print(f"  차트 {len(charts)}장: {[p.name for p in charts]}")

    ctx = {
        "spec": "ff5_umd", "beta_window": 250, "git_rev": "deadbee",
        "weekly_factors": {"mkt_rf": 0.012, "umd": -0.004, "smb": 0.002},
        "recurring_outliers": pd.DataFrame([
            {"ticker": "NVDA", "n": 3, "mean_z": 2.8, "max_abs_z": 3.4, "cum_resid_bp": 420.0},
            {"ticker": "TSLA", "n": 2, "mean_z": -2.4, "max_abs_z": 2.9, "cum_resid_bp": -310.0},
        ]),
        "next_hypothesis": None,
    }
    md = WB.build_markdown(st, ctx, [f"images/{p.name}" for p in charts], cfg)

    for sec in ("## 1. 가설", "## 2. 데이터", "## 3. 결과",
                "## 4. 판정", "## 5. 한계", "## 6. 다음 주 가설"):
        assert sec in md, f"섹션 누락: {sec}"
    assert "H0." in md and "H1." in md, "귀무/대립가설 명시 누락"
    assert "Newey-West" in md
    assert str(W.T_HURDLE) in md, "다중검정 허들 미언급"

    from src.report.builder import check_banned
    banned = check_banned(md)
    assert not banned, f"금지 표현: {banned}"

    (TMP / "weekly_post.md").write_text(md, encoding="utf-8")
    print(f"  마크다운 {len(md)}자, 6개 섹션 전부 존재")
    print(f"  제목: {WB.make_title(st)}")


def test_alphavantage_parser():
    """AV 응답 형태를 흉내 내 파서를 검증한다 (네트워크 없이)."""
    # relevance 필터
    item = {
        "title": "Nvidia beats on data center demand",
        "url": "https://example.com/a",
        "time_published": "20260724T203000",
        "summary": "Chipmaker reports strong quarter.",
        "source": "Reuters",
        "overall_sentiment_score": 0.31,
        "overall_sentiment_label": "Somewhat-Bullish",
        "ticker_sentiment": [
            {"ticker": "NVDA", "relevance_score": "0.87", "ticker_sentiment_score": "0.4"},
            {"ticker": "AMD", "relevance_score": "0.11", "ticker_sentiment_score": "0.1"},
        ],
        "topics": [{"topic": "earnings"}, {"topic": "technology"}],
    }
    ts = AV._parse_ts(item["time_published"])
    assert ts is not None and str(ts.tz) == "UTC"
    print(f"  시각 파싱: {ts}")

    keep = [t["ticker"] for t in item["ticker_sentiment"]
            if float(t["relevance_score"]) >= 0.25]
    assert keep == ["NVDA"], f"relevance 필터 실패: {keep}"
    print(f"  relevance>=0.25 필터: {keep} (AMD 0.11 제외됨)")

    mapped = sorted({AV.AV_TOPICS[t["topic"]] for t in item["topics"]
                     if t["topic"] in AV.AV_TOPICS})
    assert "실적" in mapped
    print(f"  토픽 매핑: {mapped}")

    # merge: 같은 URL은 AV 우선
    av = pd.DataFrame([{"id": "x1", "url": "https://example.com/a", "headline": "AV",
                        "summary": "", "date": pd.Timestamp("2026-07-24"),
                        "published_at": ts, "source": "AV", "tickers": ["NVDA"],
                        "av_sentiment": 0.31, "av_label": "x", "av_relevance": "{}",
                        "av_topics": ["실적"]}])
    rss = pd.DataFrame([
        {"id": "y1", "url": "https://example.com/a", "headline": "RSS dup", "summary": "",
         "date": pd.Timestamp("2026-07-24"), "published_at": ts, "source": "RSS", "tickers": []},
        {"id": "y2", "url": "https://example.com/b", "headline": "RSS new", "summary": "",
         "date": pd.Timestamp("2026-07-24"), "published_at": ts, "source": "RSS", "tickers": []},
    ])
    m = AV.merge_with_rss(av, rss)
    assert len(m) == 2, f"중복 제거 실패: {len(m)}"
    assert "RSS dup" not in set(m["headline"]), "AV 우선 실패"
    print(f"  병합: AV 1 + RSS 2 -> {len(m)}건 (중복 URL 제거)")

    seeded = AV.seed_topics(m)
    assert list(seeded[seeded["headline"] == "AV"]["topic"])[0] == ["실적"]
    print("  AV 토픽 시딩 OK -> LLM 분류 호출 절감")


def test_analytics():
    # 경로 분류
    assert AR.classify_path("/2026-07-24") == ("daily", pd.Timestamp("2026-07-24"))
    assert AR.classify_path("/2026-07-20_weekly")[0] == "weekly"
    assert AR.classify_path("/about") == (None, None)
    print("  경로 분류: daily / weekly / 기타 구분 OK")

    # 합성 GA4·AdSense 데이터. 주간 글에 의도적으로 긴 롱테일을 심는다.
    rows, earn = [], []
    for i in range(24):
        pd_ = pd.Timestamp("2026-06-01") + pd.Timedelta(days=i)
        if pd_.weekday() >= 5:
            continue
        path = f"/{pd_:%Y-%m-%d}"
        for k in range(3):                      # 일간: 3일만 읽힘
            rows.append({"date": pd_ + pd.Timedelta(days=k), "path": path,
                         "views": int(rng.integers(20, 60)), "users": 10,
                         "avg_duration_s": float(rng.uniform(40, 90))})
        earn.append({"date": pd_, "path": path, "earnings": float(rng.uniform(0.05, 0.4)),
                     "impressions": int(rng.integers(50, 200)),
                     "clicks": int(rng.integers(0, 3)), "rpm": 2.0})
    for w in range(3):                          # 주간: 14일 롱테일
        wd = pd.Timestamp("2026-06-01") + pd.Timedelta(days=7 * w)
        path = f"/{wd:%Y-%m-%d}_weekly"
        for k in range(14):
            rows.append({"date": wd + pd.Timedelta(days=k), "path": path,
                         "views": int(rng.integers(40, 110)), "users": 25,
                         "avg_duration_s": float(rng.uniform(120, 220))})
        earn.append({"date": wd, "path": path, "earnings": float(rng.uniform(0.8, 2.0)),
                     "impressions": int(rng.integers(400, 900)),
                     "clicks": int(rng.integers(2, 8)), "rpm": 3.5})

    traffic, earnings = pd.DataFrame(rows), pd.DataFrame(earn)
    sc = W.load_scorecard(TMP / "repo")
    prices = pd.DataFrame({
        "date": pd.bdate_range("2026-06-01", periods=24),
        "ticker": "SPY",
        "ret": rng.normal(0, 0.009, 24),
    })

    panel = AR.build_post_panel(traffic, earnings, sc, prices)
    assert not panel.empty
    assert set(panel["kind"]) <= {"daily", "weekly"}
    print(f"  패널 {len(panel)}편 (daily {sum(panel['kind']=='daily')}, "
          f"weekly {sum(panel['kind']=='weekly')})")

    res = AR.analyze(panel)
    assert res["available"]
    ratio = res.get("weekly_vs_daily_ratio")
    assert ratio is not None and ratio > 1.0, f"롱테일을 심었는데 감지 실패: {ratio}"
    print(f"  주간/일간 일평균 조회 비율 = {ratio:.2f} (롱테일 심은 대로 >1)")
    if res.get("q1_move_vs_views"):
        print(f"  Q1 변동폭-조회수 ρ = {res['q1_move_vs_views']['rho']:+.2f} "
              f"(무작위 심었으므로 0 근처가 정상)")

    md = AR.build_markdown(res, 30)
    assert "일간 vs 주간" in md and "조회 상위" in md
    assert "발행용이 아니라 운영 문서" in md, "운영 문서 표시 누락"
    (TMP / "analytics_report.md").write_text(md, encoding="utf-8")
    print(f"  리포트 {len(md)}자")

    # 폴백 경로: 데이터 없을 때 죽지 않아야 한다
    empty = AR.analyze(pd.DataFrame())
    assert empty == {"available": False}
    assert "데이터가 없다" in AR.build_markdown(empty, 30)
    print("  빈 데이터 폴백 OK")


if __name__ == "__main__":
    print("\n[1] 주간 통계")
    repo, sc, st, start, end = test_weekly_stats()
    print("\n[2] 판정 경계")
    test_verdict_boundaries()
    print("\n[3] 주간 리포트")
    test_weekly_report(repo, st, start, end)
    print("\n[4] Alpha Vantage 파서")
    test_alphavantage_parser()
    print("\n[5] 애널리틱스")
    test_analytics()
    print("\n전체 통과")
