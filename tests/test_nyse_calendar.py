"""NYSE 휴장일 달력 검증 (외부망 없음).

구현은 `src/nyse_holidays.py`, 배경은 CLAUDE.md 7장 16번.

이 달력이 틀리는 방향은 두 가지고 결과가 다르다.
  - **휴장일을 거래일로 본다**: `last_completed_session`이 휴장일을 대상 세션으로
    잡고, `previous_session`이 한 칸 밀리고, `news_window` 시작이 실제 직전
    세션이 아니라 휴장일 16:00이 된다. 그리고 그날이 영구히 MISSING으로 뜬다
  - **거래일을 휴장일로 본다**: 그날 기록이 통째로 빠지고 아무도 모른다

전자가 2026-09-08에 실제로 발생했다(Labor Day가 MISSING으로 잡혔다). 그래서
'연방 공휴일이지만 NYSE는 여는 날'(Columbus·Veterans)을 특히 챙겨서 본다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import nyse_holidays as NH  # noqa: E402
from src.calendar_utils import is_trading_day, news_window, previous_session, trading_days  # noqa: E402

# 2026년 NYSE 정규 휴장일. 손으로 확인한 값이다.
#   07-03 : 독립기념일(7/4)이 토요일이라 앞 금요일로 당겨 쉰다
#   04-03 : Good Friday. 연방 공휴일이 아니지만 NYSE는 휴장
HOLIDAYS_2026 = [
    "2026-01-01",  # 신정 (목)
    "2026-01-19",  # MLK
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth (금)
    "2026-07-03",  # 독립기념일 대체
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas (금)
]


def test_2026_holidays():
    got = [str(d.date()) for d in NH.holidays("2026-01-01", "2026-12-31")]
    assert got == HOLIDAYS_2026, f"2026 휴장일이 다르다:\n  got {got}\n  want {HOLIDAYS_2026}"
    print(f"  2026 정규 휴장일 {len(got)}일 일치")


def test_nyse_is_not_federal():
    """연방 공휴일 목록을 그대로 쓰면 안 된다."""
    # NYSE는 여는 연방 공휴일
    assert NH.is_trading_day("2026-10-12"), "Columbus Day는 NYSE 거래일이다"
    assert NH.is_trading_day("2026-11-11"), "Veterans Day는 NYSE 거래일이다"
    # 연방 공휴일이 아닌 NYSE 휴장일
    assert not NH.is_trading_day("2026-04-03"), "Good Friday는 NYSE 휴장일이다"
    print("  Columbus·Veterans 개장 / Good Friday 휴장")


def test_new_year_saturday_rule():
    """신정만 토요일 규칙이 다르다. 앞 금요일로 당기지 않는다.

    2022-01-01이 토요일이었고 NYSE는 2021-12-31(금)에 정상 개장했다.
    다른 휴일처럼 nearest_workday를 쓰면 이 날이 휴장으로 잡힌다.
    """
    assert pd.Timestamp("2022-01-01").day_name() == "Saturday"
    assert NH.is_trading_day("2021-12-31"), "신정이 토요일이면 앞 금요일은 개장이다"
    assert NH.is_trading_day("2022-01-03"), "1/3(월)은 개장이다"
    # 일요일이면 다음 월요일로 넘겨 쉰다
    assert pd.Timestamp("2023-01-01").day_name() == "Sunday"
    assert not NH.is_trading_day("2023-01-02"), "신정이 일요일이면 월요일 휴장이다"
    print("  토요일 신정 -> 앞 금요일 개장 / 일요일 신정 -> 월요일 휴장")


def test_juneteenth_start_date():
    """Juneteenth는 2022년부터다. 그 전 해에 소급 적용하면 과거가 틀어진다."""
    assert NH.is_trading_day("2021-06-18"), "2021년에는 Juneteenth 휴장이 없었다"
    assert not NH.is_trading_day("2022-06-20"), "2022-06-19(일) -> 20(월) 휴장"
    print("  Juneteenth 시행연도(2022) 경계 정상")


def test_ad_hoc_closures():
    """규칙으로 유도되지 않는 임시 휴장."""
    assert not NH.is_trading_day("2012-10-29"), "허리케인 샌디"
    assert not NH.is_trading_day("2012-10-30"), "허리케인 샌디"
    assert not NH.is_trading_day("2025-01-09"), "카터 국장일"
    assert NH.is_trading_day("2012-10-31"), "샌디 다음날은 개장했다"
    print("  임시 휴장 3건 반영, 인접일은 개장")


def test_calendar_utils_uses_holidays():
    """폴백이든 pandas_market_calendars든 calendar_utils 수준에서 같아야 한다."""
    days = [str(d.date()) for d in trading_days("2026-09-01", "2026-09-10")]
    assert "2026-09-07" not in days, f"Labor Day가 거래일로 잡혔다: {days}"
    assert not is_trading_day("2026-09-07")
    assert is_trading_day("2026-09-08")
    print(f"  9/1~9/10 거래일 {len(days)}일, Labor Day 제외")


def test_previous_session_skips_holiday():
    """휴장일을 건너뛰지 못하면 뉴스 창이 통째로 밀린다."""
    prev = previous_session("2026-09-08")
    assert prev == pd.Timestamp("2026-09-04"), f"직전 거래일이 틀렸다: {prev.date()}"
    start, end = news_window("2026-09-08")
    assert str(start) == "2026-09-04 20:00:00+00:00", start
    assert str(end) == "2026-09-08 20:00:00+00:00", end
    print(f"  2026-09-08 직전 세션 {prev.date()} · 창 {start} ~ {end}")


def test_matches_market_calendars_if_installed():
    """두 구현이 갈라지면 환경마다 거래일이 달라진다. CI에는 패키지가 있다."""
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        print("  pandas_market_calendars 없음 -- 교차검증 건너뜀 (CI에서는 돈다)")
        return
    sched = mcal.get_calendar("NYSE").schedule(start_date="2024-01-01", end_date="2026-12-31")
    want = {pd.Timestamp(d).normalize() for d in sched.index}
    got = set(NH.trading_days("2024-01-01", "2026-12-31"))
    only_mcal, only_builtin = sorted(want - got), sorted(got - want)
    assert not only_mcal and not only_builtin, (
        f"두 달력이 다르다.\n  mcal에만: {[str(d.date()) for d in only_mcal]}"
        f"\n  내장에만: {[str(d.date()) for d in only_builtin]}")
    print(f"  pandas_market_calendars와 2024~2026 전 구간 일치 ({len(want)}일)")


if __name__ == "__main__":
    print("\n[1] 2026 정규 휴장일")
    test_2026_holidays()
    print("\n[2] NYSE ≠ 연방 공휴일")
    test_nyse_is_not_federal()
    print("\n[3] 신정 주말 규칙")
    test_new_year_saturday_rule()
    print("\n[4] Juneteenth 시행연도")
    test_juneteenth_start_date()
    print("\n[5] 임시 휴장")
    test_ad_hoc_closures()
    print("\n[6] calendar_utils 연동")
    test_calendar_utils_uses_holidays()
    print("\n[7] 직전 세션·뉴스 창")
    test_previous_session_skips_holiday()
    print("\n[8] pandas_market_calendars 교차검증")
    test_matches_market_calendars_if_installed()
    print("\n전체 통과")
