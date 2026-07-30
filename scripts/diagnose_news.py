#!/usr/bin/env python3
"""뉴스 태깅 진단 (운영용, 발행하지 않음).

CLAUDE.md 즉시 항목 4·5번을 실측으로 답하는 도구다.

  4번: AV relevance_min 0.25가 적정한가
  5번: 이례치 종목의 '미설명' 비율이 데이터 한계인가 실제 발견인가

왜 API 재호출이 필요 없는가:
수집 시점에 tickers는 relevance_min으로 걸러서 저장하지만, av_relevance 열에는
**걸러내기 전 전체 {티커: relevance} 매핑**이 JSON으로 남아 있다. 그래서 임계값을
바꿔가며 소급 재평가할 수 있다. 무료 티어 25요청/일을 쓰지 않고 튜닝할 수 있다는
뜻이다.

핵심 구분:
'미설명'이 나오는 이유는 세 가지이고 대응이 전혀 다르다.
  (a) 임계값이 높아서 태그가 떨어졌다      -> 임계값을 내리면 해결
  (b) AV가 그 종목 기사를 아예 안 줬다      -> 데이터 커버리지 한계 (유료 소스 문제)
  (c) 기사가 있는데 그 날 움직임과 무관하다 -> 실제 발견 (뉴스가 아닌 요인)
(a)와 (b)를 (c)로 착각하면 "뉴스로 설명되지 않는 잔차가 많다"는 잘못된 결론에
도달한다. 2단계 분석 전에 반드시 갈라야 한다.

사용:
    python scripts/diagnose_news.py
    python scripts/diagnose_news.py --session 2026-07-29
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.calendar_utils import news_window  # noqa: E402
from src.collect import news as N  # noqa: E402
from src.config import load_config  # noqa: E402
from src.storage import as_list  # noqa: E402

THRESHOLDS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60]


def _relevance_pairs(win: pd.DataFrame) -> pd.DataFrame:
    """(기사 id, 티커, relevance) 롱포맷. av_relevance JSON을 펼친다."""
    rows = []
    for _, r in win.iterrows():
        raw = r.get("av_relevance")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            m = json.loads(raw)
        except Exception:
            continue
        for tkr, score in (m or {}).items():
            if tkr:
                rows.append({"id": r["id"], "ticker": tkr,
                             "relevance": float(score or 0.0)})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    news = storage.read("news")
    resid = storage.read("residuals")
    prices = storage.read("prices")
    if news.empty or resid.empty:
        print("news 또는 residuals 가 비어 있다. run_daily.py 를 먼저 돌릴 것.")
        return 1

    session = (pd.Timestamp(args.session).normalize() if args.session
               else pd.to_datetime(resid["date"]).max())
    start, end = news_window(session)
    win = N.filter_window(news, start, end)
    day_resid = resid[pd.to_datetime(resid["date"]).dt.normalize() == session]

    print("=" * 76)
    print(f"뉴스 태깅 진단 · 세션 {session.date()} · 뉴스창 {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} UTC")
    print("=" * 76)
    print(f"기사 {len(win)}건 (AV {int(win['source'].astype(str).str.startswith('AV/').sum())}건) · "
          f"잔차 {len(day_resid)}종목")
    print("**표본은 1거래일이다.** 아래 수치는 잠정이고, 20거래일쯤 쌓인 뒤 다시 볼 것.")

    pairs = _relevance_pairs(win)
    if pairs.empty:
        print("\nav_relevance가 비어 있다. AV 수집이 안 됐거나 RSS만 있는 상태다.")
        return 1

    # ---------------------------------------------- 4번: relevance 분포
    print("\n" + "-" * 76)
    print("[4] relevance_score 분포 — 임계값 0.25가 적정한가")
    print("-" * 76)
    r = pairs["relevance"]
    qs = [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    print(f"(기사 x 티커) 쌍 {len(pairs):,}건 · 고유 티커 {pairs['ticker'].nunique():,}개")
    print("분위:  " + "  ".join(f"p{int(q*100)}={r.quantile(q):.3f}" for q in qs))
    print(f"평균 {r.mean():.3f} · 중위 {r.median():.3f} · "
          f"최소 {r.min():.3f} · 최대 {r.max():.3f}")

    cur = float(cfg.get_path("news.alphavantage.relevance_min", 0.25))
    outlier_tickers = set(day_resid.loc[day_resid["is_outlier"], "ticker"]) \
        if "is_outlier" in day_resid.columns else set()
    if not outlier_tickers:
        sigma = float(cfg.get_path("model.outlier_sigma", 2.0))
        z = pd.to_numeric(day_resid.get("z"), errors="coerce")
        outlier_tickers = set(day_resid.loc[z.abs() >= sigma, "ticker"])

    universe = set(day_resid["ticker"])
    print(f"\n임계값별 효과 (이례치 {len(outlier_tickers)}종목 기준):")
    print(f"{'임계값':>7} {'태그쌍':>8} {'태그기사':>8} {'태그종목':>8} "
          f"{'이례치커버':>11} {'유니버스외':>10}")
    table = []
    for t in THRESHOLDS:
        keep = pairs[pairs["relevance"] >= t]
        tagged_tk = set(keep["ticker"])
        cov = len(outlier_tickers & tagged_tk)
        # 유니버스(=잔차 계산 대상) 밖 티커 비율. 높으면 노이즈가 늘어난 것이다.
        outside = len(tagged_tk - universe) / max(1, len(tagged_tk))
        table.append({"t": t, "pairs": len(keep), "arts": keep["id"].nunique(),
                      "tickers": len(tagged_tk), "cov": cov, "outside": outside})
        mark = "  <- 현재 설정" if abs(t - cur) < 1e-9 else ""
        print(f"{t:>7.2f} {len(keep):>8,} {keep['id'].nunique():>8,} "
              f"{len(tagged_tk):>8,} {cov:>7}/{len(outlier_tickers):<3} "
              f"{outside*100:>9.1f}%{mark}")

    tb = pd.DataFrame(table)
    best_cov = tb["cov"].max()
    knee = tb[tb["cov"] >= best_cov]["t"].max()
    obs_min = float(r.min())

    # 관측 최소값이 현재 임계값보다 크면 이 파라미터는 아무것도 걸러내지 않는다.
    # 이 경우 '올려도 손실 없음'은 사실이지만 무의미한 조언이다 -- 관측 최소값
    # 바로 아래로 붙이는 건 다음 날 경계 사례를 잃는 취약한 튜닝이다.
    if obs_min > cur:
        print(f"\n관측된 relevance 최소값이 {obs_min:.3f}로 현재 임계값 {cur:.2f}보다 크다.")
        print(f"-> 이 파라미터는 지금 아무것도 걸러내지 않는다(무효). 커버리지 병목이")
        print(f"   아니므로 유지한다. AV가 낮은 relevance를 애초에 주지 않는 것으로 보인다.")
        print(f"   임계값을 {obs_min:.2f} 근처로 붙이면 경계 사례를 잃을 뿐이다.")
    elif knee > cur:
        print(f"\n최대 이례치 커버리지 {best_cov}종목이 임계값 {knee:.2f}까지 유지된다.")
        print(f"-> 현재 {cur:.2f}는 필요보다 낮다. {knee:.2f}까지 올리면 노이즈가 줄어든다.")
    elif knee < cur:
        print(f"\n최대 이례치 커버리지 {best_cov}종목은 임계값 {knee:.2f} 이하에서만 유지된다.")
        print(f"-> 현재 {cur:.2f}가 커버리지를 깎고 있다. {knee:.2f}로 내리는 것을 검토.")
    else:
        print(f"\n최대 이례치 커버리지 {best_cov}종목은 임계값 {knee:.2f} 이하에서 유지된다.")
        print(f"-> 현재 {cur:.2f}가 그 지점이다. 유지.")

    # ------------------------------------- 5번: 미설명의 원인 분해
    print("\n" + "-" * 76)
    print("[5] '미설명'의 원인 분해 — 데이터 한계인가 실제 발견인가")
    print("-" * 76)
    if not outlier_tickers:
        print("이례치가 없다.")
        return 0

    tagged_at_cur = set(pairs.loc[pairs["relevance"] >= cur, "ticker"])
    mentioned_any = set(pairs["ticker"])              # relevance 0 이상 = 언급이라도 됨

    a = outlier_tickers & tagged_at_cur                       # 설명됨
    b = (outlier_tickers & mentioned_any) - tagged_at_cur     # 언급은 있으나 임계값 미달
    c = outlier_tickers - mentioned_any                       # AV가 아예 안 줌
    n = len(outlier_tickers)
    print(f"이례치 {n}종목:")
    print(f"  (a) 현재 임계값에서 태그됨          {len(a):>3}종목 ({len(a)/n*100:>5.1f}%)")
    print(f"  (b) 언급은 있으나 relevance 미달    {len(b):>3}종목 ({len(b)/n*100:>5.1f}%)  <- 임계값 문제")
    print(f"  (c) AV 응답에 아예 없음             {len(c):>3}종목 ({len(c)/n*100:>5.1f}%)  <- 커버리지 한계")
    if b:
        sub = pairs[pairs["ticker"].isin(b)].groupby("ticker")["relevance"].max()
        print(f"      (b)의 최대 relevance: 중위 {sub.median():.3f}, "
              f"최대 {sub.max():.3f} · 예: {', '.join(sub.nlargest(5).index)}")
    if c:
        print(f"      (c) 예: {', '.join(sorted(c)[:12])}")

    # 섹터별 커버리지. 실측에서 이게 진짜 원인이었다 -- 규모가 아니라 토픽 배치다.
    uni_path = Path(cfg.get_path("universe.constituents_file", "data/universe_sp500.csv"))
    if not uni_path.is_absolute():
        uni_path = Path(__file__).resolve().parents[1] / uni_path
    if uni_path.exists():
        uni = pd.read_csv(uni_path)
        if "sector" in uni.columns:
            sec = dict(zip(uni["ticker"], uni["sector"]))
            print("\n유니버스 섹터별 AV 커버율 (해당 세션):")
            covered_uni = pd.Series([sec[t] for t in mentioned_any if t in sec])
            comp = pd.DataFrame({
                "AV커버": covered_uni.value_counts(),
                "유니버스": uni["sector"].value_counts(),
            }).fillna(0).astype(int)
            comp["커버율%"] = (comp["AV커버"] / comp["유니버스"] * 100).round(1)
            print(comp.sort_values("커버율%").to_string())
            print("커버율이 섹터마다 크게 다르면 원인은 규모가 아니라 **토픽 배치**다.")
            print("news.alphavantage.topic_batches 가 요청하지 않은 섹터는 구조적으로 빈다.")
            print(f"무료 티어 25요청/일 중 현재 "
                  f"{cfg.get_path('news.alphavantage.max_calls_per_day')}회만 쓴다 -- "
                  f"배치를 늘릴 여유가 있다.")

    # 대형주 편향 검정: 거래대금(종가x거래량)을 규모 대리변수로 쓴다.
    # 시가총액이 없으므로 대리변수이고, 회전율 차이가 섞인다.  [검증 필요]
    day_px = prices[pd.to_datetime(prices["date"]).dt.normalize() == session]
    if not day_px.empty and {"close", "volume"} <= set(day_px.columns):
        dv = (day_px["close"] * day_px["volume"]).rename("dollar_volume")
        dv = pd.concat([day_px["ticker"], dv], axis=1).dropna()
        covered = dv[dv["ticker"].isin(a)]["dollar_volume"]
        uncov = dv[dv["ticker"].isin(b | c)]["dollar_volume"]
        if len(covered) >= 3 and len(uncov) >= 3:
            print("\n대형주 편향 검정 (규모 대리변수 = 당일 거래대금):")
            print(f"  뉴스 매칭됨   n={len(covered):>3}  중위 ${covered.median()/1e6:>10,.0f}M")
            print(f"  미설명        n={len(uncov):>3}  중위 ${uncov.median()/1e6:>10,.0f}M")
            try:
                from scipy.stats import mannwhitneyu
                u, p = mannwhitneyu(covered, uncov, alternative="greater")
                print(f"  Mann-Whitney U (매칭>미설명 단측): U={u:.0f}, p={p:.4f}")
                print("  분포가 극단적으로 치우쳐 t검정 대신 순위검정을 쓴다.")
                if p < 0.05:
                    print("  -> 미설명 종목이 유의하게 작다. **무료 소스의 대형주 편향이 실재한다.**")
                    print("     '미설명'을 실제 발견으로 읽으면 안 된다.")
                else:
                    print("  -> 규모 차이는 유의하지 않다. 미설명이 규모 편향만으로는 설명되지 않는다.")
                print(f"  주의: 1거래일 표본이고 거래대금은 시총 대리변수다. [검증 필요]")
            except ImportError:
                print("  scipy 미설치 -- 검정 생략")

    print("\n" + "-" * 76)
    print("판단 기준")
    print("-" * 76)
    print("(b)가 크면 -> relevance_min을 내린다. 임계값 튜닝으로 해결되는 문제다.")
    print("(c)가 크면 -> 데이터 커버리지 한계다. 유료 소스 없이는 '미설명'을")
    print("             실제 발견으로 해석할 수 없다. 2단계 분석의 표본을")
    print("             AV 커버리지가 있는 종목으로 한정하는 것이 정직하다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
