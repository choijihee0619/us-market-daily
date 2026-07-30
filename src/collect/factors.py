"""Fama-French 팩터 수집 + ETF 프록시.

Ken French 일간 파일은 실시간이 아니라 지연 업데이트된다. 그래서 2단 구조로 간다:
  1) 당일 리포트용  -> ETF 롱숏 스프레드 프록시 (proxy_factors)
  2) 확정 분석용    -> French 일간 FF5 + Momentum (fetch_french_daily)
French 데이터가 들어오면 같은 날짜를 upsert로 덮어써 소급 교체한다.

프록시는 French 팩터와 정확히 같지 않다. French는 가치가중 6포트폴리오 조합이고
ETF는 자체 룰·수수료·리밸런싱 시차가 있어 상관 0.7~0.9 수준으로 알려져 있다.
당일 서술용으로만 쓰고, 회귀 계수 추정에는 확정 팩터를 써야 한다.  [검증 필요]
"""
from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd
import requests

log = logging.getLogger(__name__)

FRENCH_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FF5_DAILY = f"{FRENCH_BASE}/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_DAILY = f"{FRENCH_BASE}/F-F_Momentum_Factor_daily_CSV.zip"

FACTOR_COLS = ["mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf"]


def _read_french_zip(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8", errors="ignore")

    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip()[:8].isdigit())
    rows = []
    for ln in lines[start:]:
        parts = [p.strip() for p in ln.split(",")]
        if not parts or not parts[0][:8].isdigit() or len(parts[0]) != 8:
            break
        rows.append(parts)
    df = pd.DataFrame(rows)
    df[0] = pd.to_datetime(df[0], format="%Y%m%d")
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce") / 100.0  # French는 % 단위
    return df.rename(columns={0: "date"})


def fetch_french_daily() -> pd.DataFrame:
    """확정 FF5 + UMD 일간 팩터. 실패 시 빈 DF."""
    try:
        ff = _read_french_zip(FF5_DAILY)
        ff.columns = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf"]
    except Exception as e:
        log.warning("French FF5 수집 실패: %s", e)
        return pd.DataFrame()

    try:
        mom = _read_french_zip(MOM_DAILY)
        mom.columns = ["date", "umd"]
        ff = ff.merge(mom, on="date", how="left")
    except Exception as e:
        log.warning("French Momentum 수집 실패: %s", e)
        ff["umd"] = pd.NA

    ff["source"] = "french"
    return ff[["date"] + FACTOR_COLS + ["source"]]


def proxy_factors(prices: pd.DataFrame) -> pd.DataFrame:
    """ETF 롱숏 스프레드로 만든 당일 프록시 팩터.

    mkt_rf ~ SPY - rf(무시가능수준),  smb ~ IWM-SPY,  hml ~ VLUE-SPY,
    rmw ~ QUAL-SPY,  cma ~ USMV-SPY(대용),  umd ~ MTUM-SPY
    cma를 저변동성으로 대용하는 건 이론적 근거가 약하다. 서술용 참고치로만 볼 것.
    """
    if prices.empty:
        return pd.DataFrame()
    w = prices.pivot_table(index="date", columns="ticker", values="ret")

    def spread(a: str, b: str = "SPY") -> pd.Series:
        if a in w.columns and b in w.columns:
            return w[a] - w[b]
        return pd.Series(index=w.index, dtype=float)

    out = pd.DataFrame(index=w.index)
    out["mkt_rf"] = w["SPY"] if "SPY" in w.columns else pd.NA
    out["smb"] = spread("IWM")
    out["hml"] = spread("VLUE")
    out["rmw"] = spread("QUAL")
    out["cma"] = spread("USMV")
    out["umd"] = spread("MTUM")
    out["rf"] = 0.0
    out["source"] = "etf_proxy"
    return out.reset_index()


def merge_factors(french: pd.DataFrame, proxy: pd.DataFrame) -> pd.DataFrame:
    """French가 있는 날은 French를 쓰고, 없는 최근 구간만 프록시로 채운다."""
    if french.empty:
        return proxy
    if proxy.empty:
        return french
    cutoff = pd.to_datetime(french["date"]).max()
    fill = proxy[pd.to_datetime(proxy["date"]) > cutoff]
    return pd.concat([french, fill], ignore_index=True).sort_values("date")
