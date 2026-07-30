"""귀인 계층: 그날의 움직임을 무엇이 설명했는가.

1단계에서는 '서술적 귀인'까지만 한다. 인과 주장 금지.
  - 섹터/스타일 수익률 분해
  - 잔차 횡단면 분산 중 토픽이 설명하는 비중 (Ridge, R^2)
2단계에서 Fama-MacBeth·event study·유의성 검정으로 승격한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sector_breakdown(prices: pd.DataFrame, session: pd.Timestamp,
                     sector_map: dict[str, str]) -> pd.DataFrame:
    d = pd.Timestamp(session).normalize()
    p = prices[(pd.to_datetime(prices["date"]).dt.normalize() == d)
               & (prices["ticker"].isin(sector_map))]
    if p.empty:
        return pd.DataFrame(columns=["ticker", "name", "ret"])
    out = p[["ticker", "ret"]].copy()
    out["name"] = out["ticker"].map(sector_map)
    return out.sort_values("ret", ascending=False).reset_index(drop=True)


def factor_breakdown(factors: pd.DataFrame, session: pd.Timestamp) -> dict[str, float]:
    d = pd.Timestamp(session).normalize()
    f = factors.copy()
    f["date"] = pd.to_datetime(f["date"]).dt.normalize()
    row = f[f["date"] == d]
    if row.empty:
        return {}
    r = row.iloc[-1]
    cols = ["mkt_rf", "smb", "hml", "rmw", "cma", "umd"]
    return {c: float(r[c]) for c in cols if c in r.index and pd.notna(r[c])}


def topic_regression(resid: pd.DataFrame, topic_matrix: pd.DataFrame,
                     alpha: float = 1.0) -> dict:
    """잔차를 토픽 노출에 회귀. Ridge를 쓰는 이유는 다중공선성이다.

    한 기사가 '통화정책'과 '실적'에 동시에 걸리는 게 정상이므로 토픽 열은 직교하지
    않는다. OLS로 돌리면 계수 부호가 표본마다 뒤집힌다.

    주의: 여기 R^2는 in-sample 설명력일 뿐 예측력이 아니다. 또한 잔차 e_it 자체가
    추정된 베타에서 나온 generated regressand이므로 표준오차가 과소추정된다
    (Shanken 보정 또는 block bootstrap 필요). 1단계 리포트에서는 R^2만 보고하고
    계수의 유의성은 주장하지 않는다.
    """
    if resid.empty or topic_matrix.empty:
        return {"r2": None, "coef": {}, "n": 0}

    from sklearn.linear_model import Ridge

    df = resid[["ticker", "residual"]].merge(topic_matrix, on="ticker", how="inner")
    topic_cols = [c for c in topic_matrix.columns if c != "ticker"]
    df = df.dropna(subset=["residual"])
    if len(df) < max(20, 3 * len(topic_cols)) or not topic_cols:
        return {"r2": None, "coef": {}, "n": len(df)}

    X = df[topic_cols].to_numpy(float)
    y = df["residual"].to_numpy(float)
    if np.allclose(X, 0):
        return {"r2": None, "coef": {}, "n": len(df)}

    model = Ridge(alpha=alpha).fit(X, y)
    pred = model.predict(X)
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None

    coef = {c: float(b) * 1e4 for c, b in zip(topic_cols, model.coef_)}  # bp 단위
    return {
        "r2": r2,
        "coef": dict(sorted(coef.items(), key=lambda kv: -abs(kv[1]))),
        "n": len(df),
    }


def build_topic_matrix(news: pd.DataFrame, topics: list[str]) -> pd.DataFrame:
    """종목 x 토픽 노출 행렬. 노출 = 해당 토픽 기사의 novelty 가중 감성 합."""
    if news.empty or "tickers" not in news.columns or "topic" not in news.columns:
        return pd.DataFrame()

    ex = news.explode("tickers").rename(columns={"tickers": "ticker"})
    ex = ex[ex["ticker"].notna() & (ex["ticker"] != "")]
    if ex.empty:
        return pd.DataFrame()

    rows = []
    for tkr, g in ex.groupby("ticker"):
        row = {"ticker": tkr}
        for t in topics:
            m = g["topic"].apply(lambda x, t=t: t in (x or []))
            row[t] = float(g.loc[m, "sentiment_w"].sum()) if m.any() else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def scorecard(prev_signals: pd.DataFrame, resid: pd.DataFrame,
              n_bucket: int = 5) -> dict:
    """어제 감성 신호 분위 포트폴리오의 오늘 실현 스프레드.

    이 블로그의 존재 이유. 매일 자기 신호를 채점해 공개한다.
    동일가중, 거래비용 미반영. 실제 집행 가능성 주장이 아니라 신호의 방향성 기록임.
    """
    if prev_signals is None or prev_signals.empty or resid.empty:
        return {"available": False}

    df = prev_signals[["ticker", "sent_w", "n_articles"]].merge(
        resid[["ticker", "residual", "ret"]], on="ticker", how="inner"
    )
    df = df[df["n_articles"] >= 1].dropna(subset=["sent_w", "residual"])
    if len(df) < n_bucket * 4:
        return {"available": False, "n": len(df)}

    df["bucket"] = pd.qcut(df["sent_w"].rank(method="first"), n_bucket, labels=False)
    top = df[df["bucket"] == n_bucket - 1]
    bot = df[df["bucket"] == 0]

    return {
        "available": True,
        "n": len(df),
        "n_top": len(top), "n_bottom": len(bot),
        "top_resid_bp": float(top["residual"].mean() * 1e4),
        "bottom_resid_bp": float(bot["residual"].mean() * 1e4),
        "spread_bp": float((top["residual"].mean() - bot["residual"].mean()) * 1e4),
        "hit": bool(top["residual"].mean() > bot["residual"].mean()),
    }
