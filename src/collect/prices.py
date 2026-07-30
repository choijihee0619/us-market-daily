"""가격 수집 (yfinance).

수익률은 배당·분할 조정 후 종가로 계산한다. Close를 쓰면 배당락일에 가짜 음(-)의
잔차가 생기고, 그게 뉴스 효과로 오인된다.
"""
from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def fetch_prices(tickers: Iterable[str], start: str, end: str) -> pd.DataFrame:
    """long-format: date, ticker, close, adj_close, volume, ret"""
    import yfinance as yf

    tickers = sorted(set(t for t in tickers if t))
    if not tickers:
        return pd.DataFrame()

    end_incl = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download(
        tickers,
        start=start,
        end=end_incl,
        auto_adjust=False,
        actions=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if raw is None or raw.empty:
        log.warning("yfinance가 빈 결과를 반환")
        return pd.DataFrame()

    frames = []
    for field, out in (("Close", "close"), ("Adj Close", "adj_close"), ("Volume", "volume")):
        if field not in raw.columns.get_level_values(0):
            continue
        sub = raw[field]
        if isinstance(sub, pd.Series):
            sub = sub.to_frame(tickers[0])
        s = sub.stack(future_stack=True).rename(out)
        s.index.names = ["date", "ticker"]
        frames.append(s)
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, axis=1).reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    df["adj_close"] = df["adj_close"].fillna(df["close"])

    df = df.sort_values(["ticker", "date"])
    df["ret"] = df.groupby("ticker")["adj_close"].pct_change()
    # 분할 누락 등으로 생기는 비현실적 점프 제거 (±50% 초과 일간)
    df.loc[df["ret"].abs() > 0.5, "ret"] = np.nan
    return df.dropna(subset=["adj_close"]).reset_index(drop=True)


def sp500_constituents() -> pd.DataFrame:
    """위키피디아에서 S&P 500 구성종목. 실패 시 빈 DF.

    주의: 이 목록은 '현재' 구성종목이라 과거 구간에 그대로 쓰면 생존편향이 생긴다.
    1단계 일간 리포트(기술통계·귀인)에는 문제 없지만, 백테스트로 넘어갈 때는
    시점별 구성종목 스냅샷을 매일 저장해 둔 것을 써야 한다.
    """
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            storage_options={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as e:  # pragma: no cover
        log.warning("구성종목 수집 실패: %s", e)
        return pd.DataFrame()

    df = tables[0].rename(
        columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector",
                 "GICS Sub-Industry": "industry"}
    )
    keep = [c for c in ("ticker", "name", "sector", "industry") if c in df.columns]
    df = df[keep].copy()
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)  # BRK.B -> BRK-B
    df["snapshot_date"] = pd.Timestamp.utcnow().tz_localize(None).normalize()
    return df
