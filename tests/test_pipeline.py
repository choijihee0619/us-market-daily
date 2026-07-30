"""합성 데이터 스모크 테스트.

외부망 없이 파이프라인의 수학과 렌더링이 실제로 도는지 확인한다.
알려진 정답이 있는 데이터를 넣고 되찾는 방식이라 회귀 테스트로도 쓸 수 있다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.process import attribution as A
from src.process import residual as R
from src.process import sentiment as S
from src.report import builder as B

rng = np.random.default_rng(42)


def synth(n_days: int = 320, n_tickers: int = 60):
    """알려진 베타로 수익률을 생성한다. 잔차 추정이 이 베타를 되찾아야 한다."""
    dates = pd.bdate_range("2025-01-02", periods=n_days)
    fac = pd.DataFrame({
        "date": dates,
        "mkt_rf": rng.normal(0.0004, 0.010, n_days),
        "smb": rng.normal(0, 0.005, n_days),
        "hml": rng.normal(0, 0.005, n_days),
        "rmw": rng.normal(0, 0.004, n_days),
        "cma": rng.normal(0, 0.004, n_days),
        "umd": rng.normal(0, 0.006, n_days),
        "rf": 0.00018,
    })
    cols = ["mkt_rf", "smb", "hml", "rmw", "cma", "umd"]

    tickers = [f"TK{i:03d}" for i in range(n_tickers)]
    true_beta = {t: np.concatenate([[rng.uniform(0.6, 1.5)], rng.normal(0, 0.3, 5)])
                 for t in tickers}
    rows = []
    for t in tickers:
        b = true_beta[t]
        eps = rng.normal(0, 0.012, n_days)
        r = fac[cols].to_numpy() @ b + fac["rf"].to_numpy() + eps
        rows.append(pd.DataFrame({"date": dates, "ticker": t, "ret": r,
                                  "adj_close": 100 * np.cumprod(1 + r)}))
    px = pd.concat(rows, ignore_index=True)

    # ETF 프록시도 채워 넣는다
    for etf in ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLV", "XLY", "XLP",
                "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"]:
        r = fac["mkt_rf"].to_numpy() * rng.uniform(0.8, 1.2) + rng.normal(0, 0.004, n_days)
        px = pd.concat([px, pd.DataFrame({"date": dates, "ticker": etf, "ret": r,
                                          "adj_close": 100 * np.cumprod(1 + r)})],
                       ignore_index=True)
    return px, fac, tickers, true_beta


def test_residual_recovers_beta():
    px, fac, tickers, true_beta = synth()
    session = pd.Timestamp(fac["date"].iloc[-1])
    resid = R.estimate_residuals(px, fac, session, spec="ff5_umd",
                                 window=250, min_obs=120, shrinkage="none")
    assert not resid.empty, "잔차가 비어 있음"
    assert len(resid) >= 55, f"종목 수 부족: {len(resid)}"

    err = [abs(resid.set_index("ticker").loc[t, "beta_mkt_rf"] - true_beta[t][0])
           for t in tickers if t in set(resid["ticker"])]
    mae = float(np.mean(err))
    assert mae < 0.12, f"시장베타 복원 오차 과대: MAE={mae:.4f}"
    print(f"  시장베타 복원 MAE = {mae:.4f}")

    z = resid["z"].dropna()
    assert 0.7 < z.std() < 1.4, f"표준화 잔차 분산 이상: sd={z.std():.3f}"
    print(f"  표준화 잔차 sd = {z.std():.3f} (기대 ~1.0)")
    return px, fac, resid, session


def test_sentiment():
    # LM 사전이 금융 문맥을 제대로 잡는지
    assert S.score_text("Company posts record profit, raises guidance")["tone"] > 0
    assert S.score_text("Firm warns of losses, cuts outlook after probe")["tone"] < 0
    # 부정어 뒤집기
    assert S.score_text("results were not strong")["tone"] <= 0
    # 범용 사전이 오분류하는 금융 단어가 중립으로 남는지
    neutral = S.score_text("Total liabilities and tax expense increased on capital costs")
    assert neutral["neg"] <= 1, f"금융 중립어를 부정으로 오분류: {neutral}"
    print(f"  중립 금융문장 neg={neutral['neg']} (LM 사전이 liability/tax/cost를 제외)")


def test_novelty():
    df = pd.DataFrame({
        "headline": [
            "Fed holds rates steady as inflation cools further",
            "Federal Reserve holds rates steady as inflation cools further",  # 재탕
            "Boeing wins large order from Emirates for widebody jets",
        ],
        "summary": ["", "", ""],
    })
    nov = S.compute_novelty(df)
    assert nov.iloc[1] < nov.iloc[2], f"재탕 탐지 실패: {list(nov)}"
    print(f"  novelty = {[round(x,3) for x in nov]} (2번째가 재탕)")


def test_topic_regression(resid):
    topics = ["통화정책", "실적", "지정학"]
    tickers = resid["ticker"].tolist()[:40]
    tm = pd.DataFrame({"ticker": tickers})
    for t in topics:
        tm[t] = rng.normal(0, 0.5, len(tickers))
    out = A.topic_regression(resid, tm)
    assert out["n"] > 0
    assert out["r2"] is not None
    print(f"  토픽 회귀 n={out['n']}, R2={out['r2']*100:.1f}% (무작위 노출이므로 낮아야 정상)")


def test_scorecard(resid):
    sig = pd.DataFrame({
        "ticker": resid["ticker"],
        "sent_w": rng.normal(0, 0.3, len(resid)),
        "n_articles": rng.integers(1, 6, len(resid)),
    })
    sc = A.scorecard(sig, resid)
    assert sc["available"], sc
    print(f"  스코어카드 스프레드 = {sc['spread_bp']:+.1f}bp (n={sc['n']})")


def test_report(px, fac, resid, session):
    from src.config import load_config
    cfg = load_config()

    sect_map = dict(cfg.get_path("universe.sectors", {}))
    sect_df = A.sector_breakdown(px, session, sect_map)
    macro_wide = pd.DataFrame({
        "DGS2": np.cumsum(rng.normal(0, 0.03, 130)) + 4.0,
        "DGS10": np.cumsum(rng.normal(0, 0.03, 130)) + 4.3,
        "VIXCLS": np.abs(np.cumsum(rng.normal(0, 0.5, 130))) + 14,
        "T10Y2Y": np.cumsum(rng.normal(0, 0.02, 130)) + 0.3,
        "BAMLH0A0HYM2": np.cumsum(rng.normal(0, 0.02, 130)) + 3.1,
    }, index=pd.bdate_range(end=session, periods=130))

    ctx = {
        "session": session,
        "published_kst": pd.Timestamp("2026-07-27 07:00"),
        "spec": "ff5_umd", "beta_window": 250, "git_rev": "abc1234",
        "benchmarks": {"SPY": 0.0043, "QQQ": 0.0071, "IWM": -0.0012, "DIA": 0.0021},
        "sectors": sect_df.to_dict("records"), "sectors_df": sect_df,
        "factors": {"mkt_rf": 0.0043, "smb": -0.0021, "hml": 0.0008,
                    "rmw": 0.0003, "cma": -0.0005, "umd": 0.0034},
        "macro_wide": macro_wide,
        "macro": {"DGS10": {"level": 4.28, "change": -6.0, "unit": "bp"},
                  "DGS2": {"level": 3.91, "change": -3.0, "unit": "bp"},
                  "VIXCLS": {"level": 15.4, "change": -0.8, "unit": "pt"}},
        "resid_df": resid,
        "cross_section": R.cross_section_stats(resid),
        "topic_regression": {"r2": 0.41, "n": 180,
                             "coef": {"통화정책": 23.4, "실적": -18.1, "AI/데이터센터투자": 12.7}},
        "outlier_news": {resid.nlargest(1, "z")["ticker"].iloc[0]: "Test headline for outlier"},
        "scorecard": {"available": True, "n": 180, "n_top": 36, "n_bottom": 36,
                      "top_resid_bp": 18.2, "bottom_resid_bp": -9.4,
                      "spread_bp": 27.6, "hit": True},
        "scorecard_cum": {"n_days": 22, "mean_bp": 6.4, "hit_rate": 0.59, "t_stat": 1.83},
        "upcoming": [{"time": "08:30", "name": "6월 PCE 물가", "consensus": "+0.2% m/m"}],
        "sources": ["Yahoo Finance", "FRED", "Ken French Data Library"],
    }

    from src.report import charts as C
    outdir = Path(__file__).resolve().parents[1] / "out" / "_test" / "images"
    charts = C.build_all(ctx, outdir)
    print(f"  차트 {len(charts)}장: {[p.name for p in charts]}")
    assert len(charts) >= 3, "차트 생성 실패"

    from src.llm.rule_provider import RuleProvider
    narrative = RuleProvider(cfg).write_narrative(ctx)
    md = B.build_markdown(ctx, narrative, [f"images/{p.name}" for p in charts], cfg)

    banned = B.check_banned(md)
    assert not banned, f"금지 표현이 출력에 포함됨: {banned}"

    html = B.to_naver_html(md, [str(p) for p in charts])
    assert "<table" in html and "</table>" in html
    assert html.count("<table") == html.count("</table>"), "표 태그 불균형"

    outp = outdir.parent
    (outp / "post.md").write_text(md, encoding="utf-8")
    (outp / "post.html").write_text(html, encoding="utf-8")
    print(f"  마크다운 {len(md)}자, HTML {len(html)}자 -> {outp}")
    print(f"  제목: {B.make_title(ctx)}")
    return md


def test_calendar():
    from src.calendar_utils import is_dst_in_us, kst_publish_stamp, news_window, previous_session
    s = pd.Timestamp("2026-07-24")
    a, b = news_window(s)
    assert b - a >= pd.Timedelta(hours=20), f"뉴스 창이 너무 좁음: {b-a}"
    assert str(a.tz) == "UTC" and str(b.tz) == "UTC"
    print(f"  뉴스 창: {a} ~ {b} ({b-a})")
    print(f"  직전 거래일: {previous_session(s).date()}")
    print(f"  발행 예정: {kst_publish_stamp(s)}")
    print(f"  서머타임: 7월={is_dst_in_us('2026-07-24')}, 1월={is_dst_in_us('2026-01-15')}")


if __name__ == "__main__":
    print("\n[1] 잔차 추정")
    px, fac, resid, session = test_residual_recovers_beta()
    print("\n[2] 감성 사전")
    test_sentiment()
    print("\n[3] novelty")
    test_novelty()
    print("\n[4] 토픽 회귀")
    test_topic_regression(resid)
    print("\n[5] 스코어카드")
    test_scorecard(resid)
    print("\n[6] 캘린더")
    test_calendar()
    print("\n[7] 리포트 렌더링")
    test_report(px, fac, resid, session)
    print("\n전체 통과")
