"""위험모형 잔차 계산.

중요: 이건 예측 모형이 아니라 '귀인 모형'이다.
FF5는 수익률을 예측하지 않는다. 알려진 위험 노출로 설명되는 부분을 걷어내
'설명되지 않는 움직임' e_it 를 남기는 도구다. 뉴스가 설명하는 건 바로 이 잔차다.

    r_it - rf_t = a_i + b_i'(f_t) + e_it

기본 스펙은 FF5 + UMD(Carhart 모멘텀). 모멘텀을 넣는 이유:
FF5에 모멘텀이 없어서 일간 잔차에 모멘텀 노출이 그대로 남고, 그걸 뉴스 효과로
오인하게 된다. Fama-French(2015)도 FF5에서 HML이 상당 부분 중복(redundant)임을
지적했으므로, HML 계수 해석은 보수적으로 할 것.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

SPECS: dict[str, list[str]] = {
    "capm": ["mkt_rf"],
    "ff3": ["mkt_rf", "smb", "hml"],
    "ff5": ["mkt_rf", "smb", "hml", "rmw", "cma"],
    "ff5_umd": ["mkt_rf", "smb", "hml", "rmw", "cma", "umd"],
}


def _ols(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """절편 포함 OLS. 특이행렬이면 pinv로 폴백."""
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    except np.linalg.LinAlgError:  # pragma: no cover
        beta = np.linalg.pinv(Xc) @ y
    return beta


def estimate_residuals(
    prices: pd.DataFrame,
    factors: pd.DataFrame,
    session: pd.Timestamp,
    spec: str = "ff5_umd",
    window: int = 250,
    min_obs: int = 120,
    shrinkage: str = "vasicek",
) -> pd.DataFrame:
    """세션일 하루치 잔차를 종목별로 계산.

    베타는 [session-window, session) 구간으로 추정한다. session 당일을 추정에
    포함하면 그날의 뉴스 충격이 베타에 흡수되어 잔차가 축소된다(look-ahead).
    """
    cols = SPECS.get(spec, SPECS["ff5_umd"])
    session = pd.Timestamp(session).normalize()

    f = factors.copy()
    f["date"] = pd.to_datetime(f["date"]).dt.normalize()
    f = f.set_index("date").sort_index()
    have = [c for c in cols if c in f.columns and f[c].notna().any()]
    if not have:
        log.warning("사용 가능한 팩터 없음")
        return pd.DataFrame()
    if len(have) < len(cols):
        log.warning("팩터 일부 누락 -> 축소 스펙 사용: %s", have)
    rf = f["rf"] if "rf" in f.columns else pd.Series(0.0, index=f.index)

    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"]).dt.normalize()
    p = p[p["date"] <= session]

    est_dates = f.index[(f.index < session) & (f.index >= session - pd.Timedelta(days=int(window * 1.6)))]
    est_dates = est_dates[-window:]

    rows = []
    for tkr, g in p.groupby("ticker"):
        g = g.set_index("date").sort_index()
        if session not in g.index or pd.isna(g.loc[session, "ret"]):
            continue

        idx = g.index.intersection(est_dates)
        if len(idx) < min_obs:
            continue

        y = (g.loc[idx, "ret"] - rf.reindex(idx).fillna(0.0)).to_numpy(float)
        X = f.loc[idx, have].to_numpy(float)
        ok = ~(np.isnan(y) | np.isnan(X).any(axis=1))
        if ok.sum() < min_obs:
            continue
        y, X = y[ok], X[ok]

        beta = _ols(y, X)
        fitted = np.column_stack([np.ones(len(X)), X]) @ beta
        resid_hist = y - fitted
        sigma = float(np.std(resid_hist, ddof=len(beta)))

        f_now = f.loc[session, have].to_numpy(float) if session in f.index else None
        if f_now is None or np.isnan(f_now).any():
            continue
        rf_now = float(rf.get(session, 0.0) or 0.0)
        actual = float(g.loc[session, "ret"]) - rf_now
        expected = float(beta[0] + f_now @ beta[1:])
        resid = actual - expected

        row = {
            "date": session,
            "ticker": tkr,
            "ret": float(g.loc[session, "ret"]),
            "expected": expected,
            "residual": resid,
            "resid_sigma": sigma,
            "z": resid / sigma if sigma > 0 else np.nan,
            "alpha": float(beta[0]),
            "n_obs": int(ok.sum()),
            "spec": "+".join(have),
        }
        for name, b in zip(have, beta[1:]):
            row[f"beta_{name}"] = float(b)
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    if shrinkage == "vasicek" and "beta_mkt_rf" in out.columns:
        out["beta_mkt_rf_raw"] = out["beta_mkt_rf"]
        out["beta_mkt_rf"] = _vasicek(out["beta_mkt_rf"], out["resid_sigma"], out["n_obs"])
    elif shrinkage == "blume" and "beta_mkt_rf" in out.columns:
        out["beta_mkt_rf_raw"] = out["beta_mkt_rf"]
        out["beta_mkt_rf"] = 0.67 * out["beta_mkt_rf"] + 0.33  # Blume(1971) 고전 계수

    return out


def _vasicek(beta: pd.Series, sigma: pd.Series, n: pd.Series) -> pd.Series:
    """Vasicek(1973) 베이지안 축소: 추정오차가 큰 베타를 횡단면 평균 쪽으로 당긴다.

    w = var_cross / (var_cross + var_beta_i),  beta_adj = w*beta_i + (1-w)*beta_bar
    """
    beta_bar = float(beta.mean())
    var_cross = float(beta.var(ddof=1)) or 1e-6
    # SE(beta) 근사: sigma / (sqrt(n) * sd(factor)) -- 팩터 sd를 1%로 가정한 스케일
    se2 = (sigma / np.sqrt(np.maximum(n, 1)) / 0.01) ** 2
    w = var_cross / (var_cross + se2.clip(lower=1e-9))
    return w * beta + (1 - w) * beta_bar


def cross_section_stats(resid: pd.DataFrame, sigma_cut: float = 2.0) -> dict:
    """리포트 3번 블록(이례치)용 요약."""
    if resid.empty:
        return {"n": 0}
    z = resid["z"].dropna()
    return {
        "n": int(len(resid)),
        "mean_resid_bp": float(resid["residual"].mean() * 1e4),
        "dispersion_bp": float(resid["residual"].std() * 1e4),
        "n_up_outlier": int((z > sigma_cut).sum()),
        "n_down_outlier": int((z < -sigma_cut).sum()),
        "top": resid.nlargest(10, "z")[["ticker", "ret", "residual", "z"]].to_dict("records"),
        "bottom": resid.nsmallest(10, "z")[["ticker", "ret", "residual", "z"]].to_dict("records"),
    }
