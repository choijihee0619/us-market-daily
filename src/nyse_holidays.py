"""NYSE 휴장일 — 외부 패키지 없이 도는 내장 달력.

## 왜 필요한가

`calendar_utils.trading_days()` 는 `pandas_market_calendars` 를 쓰고, 없으면
**주말만 제외하는 근사로 조용히 폴백**했다. 그 패키지는 `requirements.txt` 에
있으므로 CI에는 있고 로컬 개발 환경에는 없을 수 있다. 즉 **같은 코드가 환경에
따라 다른 거래일을 돌려준다.**

이게 왜 나쁜지는 폴백이 바꾸는 것들을 보면 된다.
  - `last_completed_session()` : 휴장일을 거래일로 착각해 그날을 대상 세션으로 잡는다
  - `previous_session()`       : 직전 거래일이 한 칸 밀린다
  - `news_window()`            : 창의 시작이 실제 직전 세션이 아니라 휴장일 16:00이 된다
  - `freshness.gap_report()`   : 휴장일이 영구히 MISSING으로 뜬다

실제로 2026-09-08 점검에서 Labor Day(2026-09-07)가 MISSING으로 잡혔다.
`freshness` 모듈이 스스로 경고했던 거짓 양성이다 — "매일 뜨는 거짓 경고는 곧
무시되고, 그러면 진짜 경고도 같이 죽는다." 그때 진짜 갭 4건이 같이 있었다.

그래서 폴백을 **주말 근사에서 실제 NYSE 규칙으로 올린다.** 이 프로젝트 규약이
"키가 없어도 파이프라인이 끝까지 돌아야 한다"이고 테스트가 외부망 없이 돌아야
하므로, 달력도 선택 의존성에 기대면 안 된다.

## NYSE 규칙에서 주의할 점

- **연방 공휴일과 다르다.** Good Friday는 연방 공휴일이 아니지만 NYSE는 쉰다.
  반대로 Columbus Day와 Veterans Day는 연방 공휴일이지만 NYSE는 연다.
- **신정의 토요일 규칙이 다르다.** 다른 휴일은 토요일이면 앞 금요일로 당겨
  쉬지만(`nearest_workday`), 1월 1일이 토요일이면 NYSE는 **전날 금요일에 연다.**
  그래서 신정만 `sunday_to_monday` 를 쓴다.
- **임시 휴장이 있다.** 규칙으로 유도되지 않으므로 목록으로 관리한다
  (허리케인 샌디, 전직 대통령 국장일 등).

## 한계

**반일장(조기 마감)은 다루지 않는다.** 추수감사절 다음날, 성탄 전날, 7월 3일
등은 13:00 ET에 마감하는데 이 모듈은 그날을 정상 거래일로만 판정한다.
`MARKET_CLOSE_HOUR = 16` 을 그대로 쓰므로 그런 날 뉴스 창이 3시간 넓어진다.
연 3~4일이고 신호에 미치는 영향은 `[검증 필요 — 미측정]`.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

# 규칙으로 유도되지 않는 임시 휴장. 날짜를 늘릴 때는 출처를 주석으로 남길 것.
AD_HOC_CLOSURES = (
    "2001-09-11", "2001-09-12", "2001-09-13", "2001-09-14",  # 9·11 테러
    "2004-06-11",                                            # 레이건 국장일
    "2007-01-02",                                            # 포드 국장일
    "2012-10-29", "2012-10-30",                              # 허리케인 샌디
    "2018-12-05",                                            # 부시 국장일
    "2025-01-09",                                            # 카터 국장일
)


class NYSECalendar(AbstractHolidayCalendar):
    """NYSE 정규 휴장일 규칙.

    Juneteenth는 2022년부터, MLK는 1998년부터 적용된다. 이 프로젝트 데이터는
    2025-03 이후지만 백테스트가 과거로 내려갈 수 있어 시작연도를 명시한다.
    """

    rules = [
        # 신정만 sunday_to_monday. 토요일이면 NYSE는 앞 금요일에 연다
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,                      # 연방 공휴일이 아니지만 NYSE는 휴장
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date=dt.datetime(2022, 6, 19),
                observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=8)
def _holidays_cached(start: str, end: str) -> tuple:
    idx = NYSECalendar().holidays(pd.Timestamp(start), pd.Timestamp(end))
    days = set(pd.DatetimeIndex(idx).normalize())
    days |= {pd.Timestamp(d) for d in AD_HOC_CLOSURES
             if pd.Timestamp(start) <= pd.Timestamp(d) <= pd.Timestamp(end)}
    return tuple(sorted(days))


def holidays(start, end) -> pd.DatetimeIndex:
    """[start, end] 구간의 NYSE 휴장일 (주말 제외한 평일 휴장)."""
    return pd.DatetimeIndex(_holidays_cached(str(pd.Timestamp(start).date()),
                                             str(pd.Timestamp(end).date())))


def trading_days(start, end) -> pd.DatetimeIndex:
    """[start, end] 구간의 NYSE 거래일 = 평일 - 휴장일."""
    rng = pd.bdate_range(str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date()))
    if len(rng) == 0:
        return pd.DatetimeIndex([])
    hol = set(holidays(rng[0], rng[-1]))
    return pd.DatetimeIndex([d for d in rng.normalize() if d not in hol])


def is_trading_day(day) -> bool:
    d = pd.Timestamp(day).normalize()
    return len(trading_days(d, d)) == 1
