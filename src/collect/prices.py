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


# 직전 관측치와 이만큼 넘게 떨어져 있으면 그 수익률은 1일치가 아니다.
# 금->월 3일, 연휴가 끼면 4~5일. 그 위는 데이터 공백으로 본다.
MAX_RET_GAP_DAYS = 5


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

    # **순서가 중요하다. dropna가 pct_change보다 먼저 와야 한다.**
    # yf.download는 여러 티커를 한 번에 받으면 인덱스를 합집합으로 만든다.
    # signals.yahoo_signals의 BTC-USD가 토·일에도 거래하므로 주말 행이 생기고
    # 주식은 그 행이 NaN이 된다. 그 상태로 pct_change를 돌리면 **월요일 수익률이
    # 직전 행(일요일 NaN)과 비교되어 전부 NaN이 된다.** 그리고 뒤이은 dropna가
    # 주말 행을 지워 증거까지 없앤다.
    #
    # 실측(2026-09-08): SPY의 월요일 행 72개 전부 ret이 NaN이었다. 가격 확정
    # 게이트가 기준지수 ret으로 판정하므로 월요일 세션이 구조적으로 버려졌고
    # (2026-08-10·17·24가 그렇게 사라졌다), 베타 추정창에서도 월요일이 빠졌다.
    # 18개월간 무증상이었다.
    df = df.dropna(subset=["adj_close"])
    df["ret"] = df.groupby("ticker")["adj_close"].pct_change()

    # 행이 실제로 빠진 구간(상장 정지, 데이터 공백)에서 다일 수익률이 1일치로
    # 둔갑하지 않게 자른다. 금->월이 3일, 연휴가 끼면 4~5일이라 그 위를 자른다.
    gap_days = df.groupby("ticker")["date"].diff().dt.days
    df.loc[gap_days > MAX_RET_GAP_DAYS, "ret"] = np.nan

    # 분할 누락 등으로 생기는 비현실적 점프 제거 (±50% 초과 일간)
    df.loc[df["ret"].abs() > 0.5, "ret"] = np.nan
    return df.reset_index(drop=True)


def sp500_constituents() -> pd.DataFrame:
    """위키피디아에서 S&P 500 구성종목. 실패 시 빈 DF.

    주의: 이 목록은 '현재' 구성종목이라 과거 구간에 그대로 쓰면 생존편향이 생긴다.
    1단계 일간 리포트(기술통계·귀인)에는 문제 없지만, 백테스트로 넘어갈 때는
    시점별 구성종목 스냅샷을 매일 저장해 둔 것을 써야 한다.
    """
    # pd.read_html(url)에 URL을 직접 주면 내부적으로 urllib을 쓴다. macOS의
    # python.org framework 빌드는 시스템 인증서 저장소를 참조하지 않아
    # CERTIFICATE_VERIFY_FAILED로 조용히 실패한다(2026-07-30 실측). requests는
    # certifi 번들을 쓰므로 문제가 없고, 이 프로젝트의 다른 수집기도 전부 requests다.
    # 그래서 내려받기와 파싱을 분리한다.
    import io

    import requests

    try:
        r = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
        )
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
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
