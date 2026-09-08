"""월요일 수익률이 통째로 NaN이 되던 버그 회귀 테스트 (외부망 없음).

## 무엇이 일어났나

`yf.download` 는 여러 티커를 한 번에 받으면 **인덱스를 합집합으로** 만든다.
`signals.yahoo_signals` 에 BTC-USD가 있어서 토·일 행이 생기고, 주식은 그 행이
NaN이 된다. 그 상태로 `groupby("ticker")["adj_close"].pct_change()` 를 돌리면
**월요일 수익률이 직전 행(일요일 NaN)과 비교되어 전부 NaN이 된다.**
그리고 다음 줄의 `dropna(subset=["adj_close"])` 가 주말 행을 지워 증거까지 없앤다.

실측(2026-09-08): SPY의 월요일 행 **72개 전부** ret이 NaN이었다. 주말 행은
BTC-USD 153개만 남아 있어 겉보기에는 멀쩡했다. 그래서 무증상으로 18개월을 갔다.

## 왜 치명적인가

`run_daily` 의 가격 확정 게이트가 기준지수의 ret이 NaN이면 종료코드 2를 낸다.
그래서 **월요일 세션이 구조적으로 버려졌다.** 2026-08-10·17·24가 그렇게 사라졌고
(Actions 로그에 "기준지수 확보 실패"로 남아 있다), 잔차 기록에 월요일은
2026-08-31 하루뿐이다.

게다가 베타 추정창이 이 가격을 그대로 읽으므로 **모든 세션의 회귀에서 월요일
관측치가 빠진다.** 월요일 효과는 알려진 캘린더 아노말리라 표본에서 요일 하나가
통째로 빠지는 건 그냥 표본 축소가 아니다.

## 고친 방식

`dropna` 를 `pct_change` **앞으로** 옮긴다. 그러면 주식의 월요일 직전 행이
금요일이 된다. 대신 행이 실제로 빠진 구간(상장 정지 등)에서 다일 수익률이
1일치로 둔갑하지 않도록 날짜 간격이 `MAX_RET_GAP_DAYS` 를 넘으면 NaN으로 자른다.
금→월이 3일, 연휴가 끼면 4~5일이라 그 위를 자른다.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collect import prices as P  # noqa: E402

EQUITIES = ["AAPL", "MSFT", "SPY"]
CRYPTO = "BTC-USD"


def _fake_raw(start="2026-08-03", end="2026-08-21", gap_ticker=None) -> pd.DataFrame:
    """yfinance가 돌려주는 모양을 흉내낸다.

    핵심은 **인덱스가 합집합**이라는 것. 크립토 때문에 주말 행이 생기고
    주식은 그 행이 NaN이다. 이게 실제 반환 형태다.
    """
    days = pd.date_range(start, end, freq="D")                 # 주말 포함
    weekdays = set(pd.bdate_range(start, end))
    cols, data = [], {}
    for field in ("Close", "Adj Close", "Volume"):
        for t in EQUITIES + [CRYPTO]:
            cols.append((field, t))
            vals = []
            for i, d in enumerate(days):
                trades = (t == CRYPTO) or (d in weekdays)
                if gap_ticker == t and pd.Timestamp("2026-08-10") <= d <= pd.Timestamp("2026-08-14"):
                    trades = False                             # 상장 정지 구간
                vals.append(100.0 + i if trades else np.nan)
            data[(field, t)] = vals
    return pd.DataFrame(data, index=pd.DatetimeIndex(days, name="Date"),
                        columns=pd.MultiIndex.from_tuples(cols))


def _patch_yf(raw: pd.DataFrame):
    mod = types.ModuleType("yfinance")
    mod.download = lambda *a, **k: raw
    sys.modules["yfinance"] = mod


def test_monday_returns_survive_crypto():
    """크립토가 같은 배치에 있어도 주식 월요일 수익률이 살아야 한다."""
    _patch_yf(_fake_raw())
    df = P.fetch_prices(EQUITIES + [CRYPTO], "2026-08-03", "2026-08-21")
    df["dow"] = pd.to_datetime(df["date"]).dt.dayofweek

    mon = df[(df["dow"] == 0) & (df["ticker"].isin(EQUITIES))]
    assert len(mon) > 0, "월요일 행 자체가 없다"
    nan_ret = mon[mon["ret"].isna()]
    # 첫 월요일(08-03)은 직전 행이 없으니 NaN이 정상이다. 그 뒤 월요일은 아니다
    later = nan_ret[pd.to_datetime(nan_ret["date"]) > pd.Timestamp("2026-08-03")]
    assert later.empty, (
        f"월요일 수익률이 NaN이다 ({len(later)}건): "
        f"{[(r.ticker, str(pd.Timestamp(r.date).date())) for r in later.itertuples()]}")
    print(f"  월요일 주식 행 {len(mon)}개 · 2주차 이후 NaN 0건")


def test_weekend_rows_only_for_crypto():
    """주말 행이 주식에 남으면 거래일 계산이 오염된다."""
    _patch_yf(_fake_raw())
    df = P.fetch_prices(EQUITIES + [CRYPTO], "2026-08-03", "2026-08-21")
    wk = df[pd.to_datetime(df["date"]).dt.dayofweek >= 5]
    assert set(wk["ticker"]) <= {CRYPTO}, f"주식에 주말 행이 남았다: {set(wk['ticker'])}"
    assert len(wk) > 0, "크립토 주말 행은 남아야 한다"
    print(f"  주말 행 {len(wk)}개, 전부 {CRYPTO}")


def test_crypto_returns_intact():
    """크립토는 주말에도 거래하므로 주말 수익률이 있어야 한다."""
    _patch_yf(_fake_raw())
    df = P.fetch_prices(EQUITIES + [CRYPTO], "2026-08-03", "2026-08-21")
    btc = df[df["ticker"] == CRYPTO]
    wk = btc[pd.to_datetime(btc["date"]).dt.dayofweek >= 5]
    assert wk["ret"].notna().all(), "크립토 주말 수익률이 NaN이다"
    print(f"  {CRYPTO} 주말 수익률 {len(wk)}건 모두 유효")


def test_long_gap_return_is_dropped():
    """행이 빠진 구간의 다일 수익률을 1일치로 보고하면 안 된다."""
    _patch_yf(_fake_raw(gap_ticker="MSFT"))
    df = P.fetch_prices(EQUITIES + [CRYPTO], "2026-08-03", "2026-08-21")
    m = df[df["ticker"] == "MSFT"].sort_values("date")
    resumed = m[pd.to_datetime(m["date"]) == pd.Timestamp("2026-08-17")]
    assert len(resumed) == 1
    assert pd.isna(resumed["ret"].iloc[0]), (
        f"08-07 -> 08-17 (10일) 간격인데 수익률이 남았다: {resumed['ret'].iloc[0]}")
    # 정상 구간은 살아 있어야 한다
    ok = m[pd.to_datetime(m["date"]) == pd.Timestamp("2026-08-18")]
    assert ok["ret"].notna().iloc[0], "정상 구간까지 잘렸다"
    print(f"  10일 공백 뒤 수익률 NaN 처리, 다음 날은 정상 (MAX_RET_GAP_DAYS={P.MAX_RET_GAP_DAYS})")


if __name__ == "__main__":
    print("\n[1] 크립토 배치에서 월요일 수익률 보존")
    test_monday_returns_survive_crypto()
    print("\n[2] 주말 행은 크립토만")
    test_weekend_rows_only_for_crypto()
    print("\n[3] 크립토 주말 수익률 유지")
    test_crypto_returns_intact()
    print("\n[4] 긴 공백 뒤 다일 수익률 차단")
    test_long_gap_return_is_dropped()
    print("\n전체 통과")
