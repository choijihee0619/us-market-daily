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

## 반일장(조기 마감)도 다룬다

추수감사절 다음날·성탄 전날·7월 3일은 13:00 ET에 마감한다. 이걸 무시하면 그날
뉴스 창이 3시간 넓어져 **마감 후 뉴스가 그날 신호에 섞인다** -- look-ahead다.
연 2~4일이라 드물지만, 드문 만큼 조용히 지나간다.

규칙은 이렇다.
  - 추수감사절 다음 금요일: 항상
  - 12/24: **월~목일 때만.** 금요일이면 12/25가 토요일이라 12/24가 대체 휴장일이
    되고, 토·일이면 애초에 거래일이 아니다
  - 7/3: 7/4가 월~금이고 7/3도 거래일일 때만. 7/4가 토요일이면 7/3이 대체
    휴장일이 되고, 일요일이면 7/3은 토요일이라 거래일이 아니다
그리고 결과에서 휴장일·주말을 다시 걸러낸다.
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


EARLY_CLOSE_HOUR = 13   # ET. 정규 마감은 16:00


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


def _early_close_candidates(year: int) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []

    # 추수감사절(11월 넷째 목) 다음 금요일
    thu = pd.date_range(f"{year}-11-01", f"{year}-11-30", freq="W-THU")
    if len(thu) >= 4:
        out.append(pd.Timestamp(thu[3]) + pd.Timedelta(days=1))

    # 성탄 전날. 월~목일 때만 (금요일이면 12/25가 토요일이라 12/24가 대체 휴장일)
    dec24 = pd.Timestamp(year, 12, 24)
    if dec24.dayofweek <= 3:
        out.append(dec24)

    # 7/3. 7/4가 월~금이고 7/3도 평일일 때만
    jul4 = pd.Timestamp(year, 7, 4)
    if jul4.dayofweek <= 4:
        jul3 = jul4 - pd.Timedelta(days=1)
        if jul3.dayofweek <= 4:
            out.append(jul3)
    return out


@lru_cache(maxsize=8)
def _early_closes_cached(start: str, end: str) -> tuple:
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    hol = set(holidays(a, b))
    days = []
    for y in range(a.year, b.year + 1):
        for d in _early_close_candidates(y):
            # 휴장일·주말은 반일장이 될 수 없다
            if a <= d <= b and d not in hol and d.dayofweek < 5:
                days.append(d)
    return tuple(sorted(set(days)))


def early_closes(start, end) -> pd.DatetimeIndex:
    """[start, end] 구간의 13:00 ET 조기 마감일."""
    return pd.DatetimeIndex(_early_closes_cached(str(pd.Timestamp(start).date()),
                                                 str(pd.Timestamp(end).date())))


def is_early_close(day) -> bool:
    d = pd.Timestamp(day).normalize()
    return len(early_closes(d, d)) == 1
