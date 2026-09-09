"""세션 신선도 점검 — 기록이 마지막 거래일보다 뒤처졌는지 판정한다.

설계 배경은 `docs/SESSION_GAPS.md`. 요약하면 이 프로젝트는 스케줄 실행이
조용히 빠지는 실패를 두 번 겪었다(2026-08-03 cron 요일 오지정, 2026-08-06
GitHub 스케줄 드롭). 둘 다 **런 자체가 없어서** 빨간 X가 뜨지 않았고
사람이 눈으로 발견했다.

`calendar_utils.last_completed_session()`은 가장 최근 거래일 하나만 돌려주므로
놓친 날을 다시 방문하는 경로가 파이프라인에 없다. 그래서 구멍은 영구적이다.
이 모듈은 그 구멍을 **명시적으로 이름 붙여** 사람이 볼 수 있게 만든다.

판정 로직을 순수 함수(`gap_report`)로 분리한 이유는 합성 데이터로 검증하기
위함이다(6장 규약: 외부망 없이 돌아야 한다). 저장소·달력 접근은 호출자가 한다.

세 상태로 나눈다. 이진 판정으로는 거짓 알람이 생긴다 -- 게이트가 열린 직후
파이프라인이 아직 도는 중일 때를 '누락'으로 부르면, 매일 아침 정상 상황에서
경고가 뜨고 그 경고는 곧 무시된다. 4장의 교훈("경고 없음은 정상의 증거가
아니다")은 반대로도 성립한다. 상시 거짓 경고는 진짜 경고를 죽인다.

  OK       기대한 세션이 모두 있다
  PENDING  가장 최근 세션이 없지만 아직 유예시간 안이다 (실행 중일 수 있다)
  MISSING  유예시간을 넘겨 없다 -> 사람이 조치해야 한다
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

import pandas as pd

from .calendar_utils import (ET, MARKET_CLOSE_HOUR, SETTLE_LAG_MIN,
                             last_completed_session, market_close_hour, trading_days)

# 잔차를 기준 테이블로 삼는다. 가격만 있고 잔차가 없는 세션은 반쪽이므로
# '있다'고 부를 수 없다. 잔차가 있으면 그 위의 신호·채점도 계산됐다는 뜻이다.
PRIMARY_TABLE = "residuals"

# 유예시간. 실측 러너 지연이 +53~68분이고 2차 슬롯이 22:20 UTC라
# 정상 실행도 마감+45분에서 3시간 넘게 뒤에 끝날 수 있다.
DEFAULT_GRACE_HOURS = 3.0

OK, PENDING, MISSING = "OK", "PENDING", "MISSING"


def settle_utc(session: pd.Timestamp | str) -> pd.Timestamp:
    """그 세션의 데이터가 확정됐다고 보는 시각(UTC).

    마감 + SETTLE_LAG_MIN. 서머타임 때문에 UTC 시각이 1시간 움직이므로 ET로
    만든 뒤 변환한다. 마감 시각은 그날 기준이다 -- 반일장은 13:00이고, 16:00으로
    고정하면 그날 확정 판정이 3시간 늦어져 유예시간 안에 있는 세션을 MISSING으로
    잘못 잡을 수 있다.
    """
    d = pd.Timestamp(session).normalize()
    close_et = d.tz_localize(ET) + pd.Timedelta(
        hours=market_close_hour(d), minutes=SETTLE_LAG_MIN)
    return close_et.tz_convert("UTC")


def expected_sessions(last_session: pd.Timestamp, lookback: int) -> list[pd.Timestamp]:
    """last_session까지의 최근 `lookback`개 거래일.

    달력에서 뽑는다. 데이터에 있는 날짜에서 추론하면 누락된 날이
    애초에 기대치에서도 빠져 영원히 발견되지 않는다.
    """
    last = pd.Timestamp(last_session).normalize()
    # 거래일 lookback개를 확보하려면 달력일로 넉넉히 잡아야 한다(휴장·주말).
    start = last - pd.Timedelta(days=int(lookback * 2 + 15))
    days = trading_days(start.date(), last.date())
    days = [pd.Timestamp(d).normalize() for d in days if pd.Timestamp(d).normalize() <= last]
    return days[-lookback:] if lookback > 0 else days


def gap_report(present: Iterable, last_session: Optional[pd.Timestamp],
               now_utc: Optional[dt.datetime] = None,
               lookback: int = 30,
               grace_hours: float = DEFAULT_GRACE_HOURS,
               since: Optional[pd.Timestamp] = None) -> dict:
    """순수 판정 함수. 저장소를 읽지 않는다.

    present      기록에 실재하는 세션 날짜들 (형식 무관, Timestamp로 정규화한다)
    last_session 지금 시점에서 마감이 완료된 가장 최근 거래일 (None이면 판정 불가)
    since        이 날짜 이전은 점검하지 않는다. 생략하면 **기록의 첫 세션**을 쓴다.

    `since` 기본값이 중요하다. 갭은 기록 *내부*의 구멍이지 기록 이전의 부재가
    아니다. 이걸 빼면 lookback 창이 기록 시작(2026-07-29, 첫 실제 실행) 이전으로
    넘어가면서 그 이전 거래일 전부가 MISSING으로 잡힌다. 실측으로 24건이 떴고
    진짜는 1건(2026-08-03)이었다. 23건의 거짓 경고는 진짜 1건을 묻는다 --
    이 모듈이 막으려는 실패를 이 모듈이 만드는 셈이다.
    """
    now = pd.Timestamp(now_utc or dt.datetime.now(dt.timezone.utc))
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")

    have = {pd.Timestamp(x).normalize() for x in present if pd.notna(x)}

    if last_session is None:
        return {"status": OK, "last_session": None, "latest_present": None,
                "expected": [], "missing": [], "pending": [],
                "lag_sessions": 0, "reason": "마감된 거래일이 아직 없다"}

    exp = expected_sessions(last_session, lookback)
    floor = pd.Timestamp(since).normalize() if since is not None else (
        min(have) if have else None)
    if floor is not None:
        exp = [d for d in exp if d >= floor]
    absent = [d for d in exp if d not in have]

    # 유예시간 안의 것과 넘긴 것을 나눈다.
    pending, missing = [], []
    for d in absent:
        if now < settle_utc(d) + pd.Timedelta(hours=grace_hours):
            pending.append(d)
        else:
            missing.append(d)

    latest = max(have) if have else None
    # 뒤처진 정도는 '기대 세션 중 최신 것부터 몇 개가 비었는지'로 센다.
    lag = 0
    for d in reversed(exp):
        if d in have:
            break
        lag += 1

    status = MISSING if missing else (PENDING if pending else OK)
    return {"status": status, "last_session": pd.Timestamp(last_session).normalize(),
            "latest_present": latest, "expected": exp,
            "missing": missing, "pending": pending,
            "lag_sessions": lag, "since": floor, "reason": ""}


def sessions_present(table: str = PRIMARY_TABLE) -> set[pd.Timestamp]:
    """저장소에서 실재하는 세션 날짜를 읽는다."""
    from . import storage
    df = storage.read(table)
    if df.empty or "date" not in df.columns:
        return set()
    return {pd.Timestamp(x).normalize() for x in pd.to_datetime(df["date"]).unique()}


def audit(lookback: int = 30, grace_hours: float = DEFAULT_GRACE_HOURS,
          table: str = PRIMARY_TABLE,
          now_utc: Optional[dt.datetime] = None,
          since: Optional[pd.Timestamp] = None) -> dict:
    """저장소 + 달력을 읽어 판정까지 한 번에."""
    rep = gap_report(sessions_present(table), last_completed_session(now_utc),
                     now_utc=now_utc, lookback=lookback, grace_hours=grace_hours,
                     since=since)
    rep["table"] = table
    return rep


def format_report(rep: dict, archive_missing: Optional[list] = None) -> str:
    """사람이 읽을 형태로. 조치 명령까지 같이 찍는다."""
    L: list[str] = []
    A = L.append
    last = rep.get("last_session")
    latest = rep.get("latest_present")

    A(f"마지막 마감 거래일   {last.date() if last is not None else '—'}")
    A(f"기록의 최신 세션     {latest.date() if latest is not None else '— (기록 없음)'}"
      f"   [{rep.get('table', PRIMARY_TABLE)}]")
    A(f"뒤처진 세션 수       {rep['lag_sessions']}")
    since = rep.get("since")
    if since is not None:
        A(f"점검 시작일          {pd.Timestamp(since).date()}  "
          f"(기대 세션 {len(rep.get('expected', []))}개)")
    A("")

    if rep.get("reason"):
        A(rep["reason"])
        return "\n".join(L)

    if rep["pending"]:
        A("PENDING — 아직 유예시간 안이다. 파이프라인이 도는 중일 수 있다.")
        for d in rep["pending"]:
            A(f"  · {d.date()}  (확정 {settle_utc(d):%Y-%m-%d %H:%M} UTC)")
        A("")

    if rep["missing"]:
        A("MISSING — 유예시간을 넘겨 비어 있다. 조치가 필요하다.")
        for d in rep["missing"]:
            A(f"  · {d.date()}")
        A("")
        A("복구 (오래된 것부터 하나씩. AV 무료 한도 25요청/일, 세션당 7회를 쓴다")
        A(" -- 하루에 3세션까지만 돌릴 수 있다):")
        for d in rep["missing"][:3]:
            A(f"  python scripts/run_daily.py --session {d.date()}")
        if len(rep["missing"]) > 3:
            A(f"  ... 그리고 {len(rep['missing']) - 3}개 더 (다음 날로 나눠 돌릴 것)")
        A("")
        A("소급 실행은 실시간 실행과 같지 않다. docs/SESSION_GAPS.md 3절 계층 2 참조.")

    if rep["status"] == OK:
        A("OK — 기대한 세션이 모두 있다.")

    if archive_missing:
        A("")
        A("참고 — 잔차는 있는데 posts/ 아카이브가 없는 세션:")
        A("  " + ", ".join(str(pd.Timestamp(d).date()) for d in archive_missing))
        A("  기록은 남아 있고 발행용 원고만 없는 상태다. 갭 메움이 산출물을")
        A("  만들지 않도록 설계했기 때문에 정상일 수 있다.")

    return "\n".join(L)
