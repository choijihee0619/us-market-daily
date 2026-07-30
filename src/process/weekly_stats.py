"""주간 검증 통계.

일간 리포트가 "무슨 일이 있었나"라면 주간 글은 "내 신호가 작동하는가"다.
후자가 검색 가치도 높고 연구 기여도 여기서 나온다.

통계 설계에서 지킨 것 세 가지:

1) t-통계량은 Newey-West(HAC)로 낸다. 일별 스프레드는 자기상관이 있다.
   OLS 표준오차를 쓰면 t가 부풀려진다.
2) 적중률은 이항검정으로 p값을 함께 낸다. "적중률 60%"만 쓰면 표본이 5일인지
   200일인지 알 수 없다.
3) **다중검정 허들을 명시한다.** 매주 여러 스펙을 보다 보면 우연히 유의한 게 나온다.
   Harvey, Liu & Zhu(2016)는 팩터 발견에 |t| > 3.0을 요구했다. 2.0을 넘겼다고
   "유의하다"고 쓰지 않는다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

# Harvey-Liu-Zhu(2016)가 팩터 발견에 제안한 허들. 관행적 2.0이 아니다.
T_HURDLE = 3.0


def load_scorecard(repo_root: Path) -> pd.DataFrame:
    p = Path(repo_root) / "data" / "scorecard.json"
    if not p.exists():
        return pd.DataFrame()
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _newey_west_t(x: np.ndarray, lags: int = 5) -> tuple[float, float]:
    """평균의 HAC 표준오차와 t. statsmodels가 있으면 그걸 쓰고 없으면 직접 계산."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")

    try:
        import statsmodels.api as sm

        res = sm.OLS(x, np.ones(n)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
        return float(res.bse[0]), float(res.tvalues[0])
    except Exception:
        pass

    # 폴백: Bartlett 커널 장기분산
    e = x - x.mean()
    gamma0 = float((e @ e) / n)
    s = gamma0
    for L in range(1, min(lags, n - 1) + 1):
        w = 1.0 - L / (lags + 1)
        cov = float((e[L:] @ e[:-L]) / n)
        s += 2 * w * cov
    se = math.sqrt(max(s, 1e-18) / n)
    return se, float(x.mean() / se) if se > 0 else float("nan")


def _binom_p(k: int, n: int, p0: float = 0.5) -> float:
    """방향 적중이 동전던지기와 다른지 양측 이항검정."""
    if n == 0:
        return float("nan")
    try:
        from scipy import stats

        return float(stats.binomtest(k, n, p0, alternative="two-sided").pvalue)
    except Exception:
        pass
    # 폴백: 정규근사 (연속성 보정)
    mu, sd = n * p0, math.sqrt(n * p0 * (1 - p0))
    if sd == 0:
        return float("nan")
    z = (abs(k - mu) - 0.5) / sd
    return float(math.erfc(z / math.sqrt(2)))


def week_bounds(anchor: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """anchor가 속한 주의 월~금 (ET 거래일 기준)."""
    a = pd.Timestamp(anchor).normalize()
    mon = a - pd.Timedelta(days=int(a.weekday()))
    return mon, mon + pd.Timedelta(days=4)


def summarize(sc: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
              lags: int = 5) -> dict:
    """주간 + 누적 검증 통계."""
    if sc.empty:
        return {"available": False, "n_week": 0, "n_total": 0}

    wk = sc[(sc["date"] >= start) & (sc["date"] <= end)]
    out: dict = {
        "available": len(wk) > 0,
        "start": start, "end": end,
        "n_week": int(len(wk)),
        "n_total": int(len(sc)),
    }
    if wk.empty:
        return out

    s = pd.to_numeric(wk["spread_bp"], errors="coerce").dropna()
    out["week_mean_bp"] = float(s.mean())
    out["week_sd_bp"] = float(s.std(ddof=1)) if len(s) > 1 else float("nan")
    out["week_hits"] = int((s > 0).sum())
    out["week_hit_rate"] = float((s > 0).mean())
    out["week_best"] = {
        "date": wk.loc[s.idxmax(), "date"], "bp": float(s.max())} if len(s) else None
    out["week_worst"] = {
        "date": wk.loc[s.idxmin(), "date"], "bp": float(s.min())} if len(s) else None
    out["week_daily"] = [
        {"date": r["date"], "bp": float(r["spread_bp"]), "hit": bool(r["hit"])}
        for _, r in wk.iterrows()
    ]

    # 누적
    a = pd.to_numeric(sc["spread_bp"], errors="coerce").dropna()
    se, t = _newey_west_t(a.to_numpy(), lags=lags)
    hits = int((a > 0).sum())
    out.update({
        "cum_mean_bp": float(a.mean()),
        "cum_sd_bp": float(a.std(ddof=1)) if len(a) > 1 else float("nan"),
        "cum_se_bp": float(se),
        "cum_t": float(t),
        "cum_hits": hits,
        "cum_hit_rate": float(hits / len(a)),
        "cum_binom_p": _binom_p(hits, len(a)),
        "cum_curve": list(np.cumsum(a.to_numpy())),
        "cum_dates": list(sc.loc[a.index, "date"]),
        "t_hurdle": T_HURDLE,
        "passes_hurdle": bool(abs(t) > T_HURDLE) if not math.isnan(t) else False,
        "lags": lags,
    })

    # 연율 환산 Sharpe (거래비용 미반영, 방향성 참고치)
    if len(a) > 5 and a.std(ddof=1) > 0:
        out["ann_sharpe"] = float(a.mean() / a.std(ddof=1) * math.sqrt(252))
    return out


def verdict(stats: dict) -> tuple[str, str]:
    """가설 판정. 과잉 주장을 막기 위해 3단계만 둔다."""
    n = stats.get("n_total", 0)
    t = stats.get("cum_t", float("nan"))
    if n < 20 or math.isnan(t):
        return ("판단 보류",
                f"누적 {n}거래일로는 표본이 부족하다. 최소 20거래일 이상 쌓인 뒤 재평가한다.")
    if abs(t) > T_HURDLE:
        return ("기각 실패 (신호 존재 가능)",
                f"|t|={abs(t):.2f} 로 Harvey-Liu-Zhu 허들 {T_HURDLE}을 넘었다. "
                "다만 이는 단일 신호에 대한 결과이며, 거래비용과 실행가능성은 반영되지 않았다.")
    if abs(t) > 2.0:
        return ("결정적이지 않음",
                f"|t|={abs(t):.2f} 로 관행적 기준 2.0은 넘지만 다중검정을 고려한 허들 "
                f"{T_HURDLE}에는 못 미친다. 우연일 가능성을 배제할 수 없다.")
    return ("귀무가설 기각 실패",
            f"|t|={abs(t):.2f}. 뉴스 감성이 익일 초과수익률을 설명한다는 증거를 "
            "이 표본에서는 찾지 못했다.")


def weekly_factors(factors: pd.DataFrame, start, end) -> dict[str, float]:
    """주간 누적 팩터 수익률 (복리)."""
    if factors is None or factors.empty:
        return {}
    f = factors.copy()
    f["date"] = pd.to_datetime(f["date"]).dt.normalize()
    wk = f[(f["date"] >= pd.Timestamp(start)) & (f["date"] <= pd.Timestamp(end))]
    if wk.empty:
        return {}
    out = {}
    for c in ["mkt_rf", "smb", "hml", "rmw", "cma", "umd"]:
        if c in wk.columns and wk[c].notna().any():
            out[c] = float(np.prod(1 + wk[c].fillna(0).to_numpy()) - 1)
    return out


def recurring_outliers(residuals: pd.DataFrame, start, end, sigma: float = 2.0,
                       top_n: int = 8) -> pd.DataFrame:
    """주중 반복해서 ±2σ를 벗어난 종목.

    한 주에 두 번 이상 이례치로 잡히면 (a) 실제 사건이 진행 중이거나
    (b) 그 종목의 베타 추정이 잘못됐다는 신호다. 후자라면 모형 문제다.
    """
    if residuals is None or residuals.empty:
        return pd.DataFrame()
    r = residuals.copy()
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    wk = r[(r["date"] >= pd.Timestamp(start)) & (r["date"] <= pd.Timestamp(end))]
    wk = wk[wk["z"].abs() >= sigma]
    if wk.empty:
        return pd.DataFrame()
    g = wk.groupby("ticker").agg(
        n=("z", "size"),
        mean_z=("z", "mean"),
        max_abs_z=("z", lambda x: float(x.abs().max())),
        cum_resid_bp=("residual", lambda x: float(x.sum() * 1e4)),
    ).reset_index()
    return g[g["n"] >= 2].sort_values(["n", "max_abs_z"], ascending=False).head(top_n)
