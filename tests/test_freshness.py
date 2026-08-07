"""세션 누락 감지 테스트 (외부망 없음).

설계는 `docs/SESSION_GAPS.md`, 구현은 `src/freshness.py`.

이 감지기의 실패 양상은 두 방향이다. 둘 다 검사한다.
  - 못 잡는다 (거짓 음성): 구멍이 있는데 OK라고 한다
  - 너무 잡는다 (거짓 양성): 정상인데 MISSING이라고 한다

**후자가 더 위험하다.** 매일 뜨는 거짓 경고는 곧 무시되고, 그러면 진짜 경고도
같이 죽는다. 4장의 교훈("경고 없음은 정상의 증거가 아니다")이 반대로도 성립한다.
실제로 첫 실행에서 거짓 양성 23건이 진짜 1건을 묻었다 -- `since` 기본값
회귀 테스트가 그것이다.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import freshness as F  # noqa: E402

# 2026-07-29(수) ~ 08-06(목) 의 NYSE 거래일. 08-01·02 는 주말이다.
SESSIONS = [pd.Timestamp(x) for x in
            ["2026-07-29", "2026-07-30", "2026-07-31",
             "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]]
LAST = pd.Timestamp("2026-08-06")

# 08-06 확정(마감+45분)에서 충분히 지난 시각. 여름이라 마감 20:00 UTC.
NOW = dt.datetime(2026, 8, 7, 6, 0, tzinfo=dt.timezone.utc)


def _rep(present, **kw):
    kw.setdefault("now_utc", NOW)
    kw.setdefault("lookback", 30)
    return F.gap_report(present, LAST, **kw)


def test_calendar_matches_assumption():
    """전제 확인. 달력이 다르면 아래 테스트가 전부 무의미해진다."""
    exp = F.expected_sessions(LAST, 7)
    assert exp == SESSIONS, f"거래일 전제 불일치: {[str(d.date()) for d in exp]}"
    print("  거래일 전제 OK (7개, 주말 제외)")


def test_all_present_is_ok():
    r = _rep(SESSIONS)
    assert r["status"] == F.OK, r
    assert r["missing"] == [] and r["pending"] == []
    assert r["lag_sessions"] == 0
    print("  전부 있으면 OK")


def test_interior_gap_detected():
    """08-03(월) 하나만 빠진 실제 사례."""
    present = [d for d in SESSIONS if d != pd.Timestamp("2026-08-03")]
    r = _rep(present)
    assert r["status"] == F.MISSING, r
    assert r["missing"] == [pd.Timestamp("2026-08-03")], \
        [str(d.date()) for d in r["missing"]]
    # 머리는 최신이므로 '뒤처진 세션 수'는 0이어야 한다. 내부 구멍과
    # 뒤처짐은 다른 개념이고 섞으면 둘 다 못 읽는다.
    assert r["lag_sessions"] == 0, r["lag_sessions"]
    print("  내부 구멍 1건 탐지, lag=0 (머리는 최신)")


def test_prehistory_is_not_a_gap():
    """회귀 테스트 -- 기록 시작 이전은 구멍이 아니다.

    `since` 기본값이 없으면 lookback 창이 기록 시작 이전으로 넘어가면서
    그 이전 거래일 전부가 MISSING으로 잡힌다. 첫 구현이 정확히 이랬고
    24건 중 진짜는 1건이었다. 23건의 거짓 경고는 진짜 1건을 묻는다.
    """
    r = _rep(SESSIONS, lookback=90)
    assert r["status"] == F.OK, \
        f"기록 이전을 구멍으로 셌다: {[str(d.date()) for d in r['missing']][:5]}"
    assert r["since"] == SESSIONS[0]
    assert len(r["expected"]) == len(SESSIONS)
    # since를 명시하면 그 날짜부터 본다 (오래된 구멍을 일부러 조사할 때)
    r2 = _rep(SESSIONS, lookback=90, since=pd.Timestamp("2026-07-01"))
    assert r2["status"] == F.MISSING and len(r2["missing"]) > 10
    print(f"  기록 이전 무시 OK · since 명시 시 {len(r2['missing'])}건 조사")


def test_grace_separates_pending_from_missing():
    """확정 직후의 부재는 MISSING이 아니다. 파이프라인이 도는 중일 수 있다."""
    present = SESSIONS[:-1]                      # 08-06 없음
    settle = F.settle_utc(LAST)                  # 2026-08-06 20:45 UTC

    early = _rep(present, now_utc=(settle + pd.Timedelta(hours=1)).to_pydatetime())
    assert early["status"] == F.PENDING, early
    assert early["pending"] == [LAST] and early["missing"] == []

    late = _rep(present, now_utc=(settle + pd.Timedelta(hours=4)).to_pydatetime())
    assert late["status"] == F.MISSING, late
    assert late["missing"] == [LAST]

    # 뒤처진 정도는 유예 여부와 무관하게 센다
    assert early["lag_sessions"] == 1 and late["lag_sessions"] == 1
    print("  유예 1h -> PENDING · 4h -> MISSING (기본 유예 3h)")


def test_lag_counts_consecutive_tail():
    """머리에서 연속으로 빈 개수를 센다."""
    r = _rep(SESSIONS[:-3])                      # 08-04·05·06 없음
    assert r["lag_sessions"] == 3, r["lag_sessions"]
    assert r["status"] == F.MISSING
    print("  연속 3세션 누락 -> lag=3")


def test_empty_record_does_not_crash():
    """기록이 아예 없을 때. 초기 상태에서 스크립트가 죽으면 안 된다."""
    r = _rep([])
    assert r["latest_present"] is None
    assert r["since"] is None
    # 기록이 없으면 바닥이 없으므로 lookback 전체가 대상이 된다.
    assert len(r["expected"]) > 0
    print("  빈 기록에서도 판정 가능")


def test_no_completed_session():
    """마감된 거래일이 없으면(연휴 등) 판정을 보류한다."""
    r = F.gap_report(SESSIONS, None, now_utc=NOW)
    assert r["status"] == F.OK and r["reason"]
    print(f"  마감 세션 없음 -> 보류 ({r['reason']})")


def test_format_report_renders():
    present = [d for d in SESSIONS if d != pd.Timestamp("2026-08-03")]
    out = F.format_report(_rep(present))
    assert "2026-08-03" in out
    assert "run_daily.py --session 2026-08-03" in out, "복구 명령이 없다"
    assert "SESSION_GAPS" in out, "소급 실행 주의 안내가 없다"
    print("  보고서에 조치 명령 포함")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"[{name}]")
            fn()
    print("\n전체 통과")


if __name__ == "__main__":
    main()
