"""거래일·시간대 유틸.

핵심 원칙 두 가지:
1) cron을 KST로 고정하지 않는다. 미국 서머타임 전환 때 마감 시각이 1시간 밀리기 때문에
   "직전 미국 거래일의 마감이 실제로 끝났는가"를 코드에서 판정한다.
2) 뉴스 신호 창은 [직전 거래일 16:00 ET, 대상 거래일 16:00 ET) 로 잘라
   look-ahead bias를 차단한다.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Optional

import pandas as pd

ET = "America/New_York"
KST = "Asia/Seoul"
MARKET_CLOSE_HOUR = 16  # ET 기준 정규장 마감
SETTLE_LAG_MIN = 45     # 마감 후 데이터 확정까지 여유


@lru_cache(maxsize=1)
def _nyse():
    try:
        import pandas_market_calendars as mcal

        return mcal.get_calendar("NYSE")
    except Exception:  # pragma: no cover - 폴백
        return None


def trading_days(start: str | dt.date, end: str | dt.date) -> pd.DatetimeIndex:
    """NYSE 거래일. pandas_market_calendars가 없으면 주말만 제외하는 근사로 폴백."""
    cal = _nyse()
    if cal is not None:
        sched = cal.schedule(start_date=str(start), end_date=str(end))
        return pd.DatetimeIndex(sched.index).normalize()
    rng = pd.bdate_range(str(start), str(end))
    return pd.DatetimeIndex(rng).normalize()


def last_completed_session(now_utc: Optional[dt.datetime] = None) -> Optional[pd.Timestamp]:
    """지금 시점에서 '마감이 완료된' 가장 최근 거래일을 반환.

    아직 어떤 거래일도 마감되지 않았으면 None.
    """
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    now_et = pd.Timestamp(now).tz_convert(ET)

    days = trading_days(
        (now_et - pd.Timedelta(days=14)).date(), (now_et + pd.Timedelta(days=1)).date()
    )
    for day in reversed(days):
        close_et = pd.Timestamp(day).tz_localize(ET) + pd.Timedelta(
            hours=MARKET_CLOSE_HOUR, minutes=SETTLE_LAG_MIN
        )
        if now_et >= close_et:
            return pd.Timestamp(day).normalize()
    return None


def previous_session(day: pd.Timestamp | str) -> pd.Timestamp:
    d = pd.Timestamp(day).normalize()
    days = trading_days((d - pd.Timedelta(days=20)).date(), d.date())
    days = days[days < d]
    if len(days) == 0:
        raise ValueError(f"{d.date()} 이전 거래일을 찾지 못함")
    return pd.Timestamp(days[-1]).normalize()


def news_window(session: pd.Timestamp | str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """대상 거래일의 뉴스 수집 창 (UTC, 좌폐우개).

    [직전 거래일 16:00 ET, 대상 거래일 16:00 ET)
    이 창 안의 뉴스만 해당 세션의 신호로 쓴다. 창 밖 기사를 넣으면 look-ahead가 된다.
    """
    d = pd.Timestamp(session).normalize()
    prev = previous_session(d)
    start = pd.Timestamp(prev).tz_localize(ET) + pd.Timedelta(hours=MARKET_CLOSE_HOUR)
    end = pd.Timestamp(d).tz_localize(ET) + pd.Timedelta(hours=MARKET_CLOSE_HOUR)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def kst_publish_stamp(session: pd.Timestamp | str) -> pd.Timestamp:
    """해당 세션 리포트의 KST 발행 예정 시각 (다음 날 07:00 KST 근방)."""
    d = pd.Timestamp(session).normalize()
    close_utc = (pd.Timestamp(d).tz_localize(ET) + pd.Timedelta(hours=MARKET_CLOSE_HOUR)).tz_convert("UTC")
    kst = close_utc.tz_convert(KST)
    target = kst.normalize() + pd.Timedelta(hours=7)
    if target <= kst:
        target += pd.Timedelta(days=1)
    return target


def is_dst_in_us(day: pd.Timestamp | str) -> bool:
    """해당일 미국 동부가 서머타임(EDT)인지. 마감→KST 변환 설명용."""
    ts = pd.Timestamp(day).normalize().tz_localize(ET)
    return bool(ts.dst())
