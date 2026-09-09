"""지수 구성종목 시점별 스냅샷 검증 (외부망 없음).

## 왜 필요한가 — 생존편향

지금 구성종목으로 과거를 보면 그 사이 편입된 종목의 성과가 소급 반영되고 퇴출된
종목은 아예 사라진다. 그래서 백테스트는 **그 시점에 우리가 알고 있던 목록**을
써야 한다(CLAUDE.md 8장 1번).

문서에는 "`snapshot_date`로 매일 저장 중이므로 시간이 지나면 스냅샷이 쌓인다"고
적혀 있었는데 **사실이 아니었다.** `resolve_universe()` 가 csv가 있으면 그대로
돌려주고 `sp500_constituents()` 를 다시 부르지 않아서, 그 기록은 아무도 호출하지
않는 죽은 경로였다. 40거래일간 스냅샷이 0건이었고 구성종목은 2026-07-30자에
얼어 있었다.

## 여기서 특히 보는 것

`universe_asof` 가 **`date` 이전 스냅샷이 없을 때 빈 결과를 준다**는 것.
가장 이른 스냅샷을 끌어다 쓰면 모르는 걸 아는 척하는 것이고, 그게 정확히
막으려던 편향을 조용히 다시 만든다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.collect import prices as P  # noqa: E402

D1, D2 = pd.Timestamp("2026-08-03"), pd.Timestamp("2026-09-01")


def _snap(date, tickers) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [date] * len(tickers),
        "ticker": list(tickers),
        "name": [f"{t} Inc" for t in tickers],
        "sector": ["Tech"] * len(tickers),
    })


def test_asof_uses_prior_snapshot():
    """`date` 이하의 최신 스냅샷을 쓴다. 최신 스냅샷이 아니다."""
    snaps = pd.concat([_snap(D1, ["AAA", "BBB"]), _snap(D2, ["AAA", "CCC", "DDD"])])
    mid = P.universe_asof(snaps, "2026-08-20")
    assert set(mid["ticker"]) == {"AAA", "BBB"}, set(mid["ticker"])
    later = P.universe_asof(snaps, "2026-09-05")
    assert set(later["ticker"]) == {"AAA", "CCC", "DDD"}
    exact = P.universe_asof(snaps, D2)
    assert set(exact["ticker"]) == {"AAA", "CCC", "DDD"}, "스냅샷 당일은 그 스냅샷이다"
    print(f"  08-20 -> {sorted(mid['ticker'])} · 09-05 -> {sorted(later['ticker'])}")


def test_asof_before_first_is_empty():
    """모르는 구간에서 가장 이른 목록을 끌어다 쓰면 편향이 조용히 생긴다."""
    snaps = _snap(D2, ["AAA", "CCC"])
    assert P.universe_asof(snaps, "2026-07-01").empty
    assert P.universe_asof(pd.DataFrame(), "2026-09-05").empty
    print("  첫 스냅샷 이전 -> 빈 결과 (앞당겨 쓰지 않는다)")


def _load_run_daily():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rd_universe", Path(__file__).resolve().parents[1] / "scripts" / "run_daily.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    return rd


def _cfg(rd, tmp: Path):
    cfg = rd.load_config()
    cfg["universe"]["constituents_file"] = str(tmp / "universe_sp500.csv")
    return cfg


def test_resolve_saves_snapshot(tmp: Path):
    rd = _load_run_daily()
    storage.DATA_DIR = tmp                                   # type: ignore[assignment]
    original = P.sp500_constituents
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return _snap(pd.Timestamp("2000-01-01"), ["AAA", "BBB"]).drop(columns=["date"])

    P.sp500_constituents = fake                              # type: ignore[assignment]
    try:
        u = rd.resolve_universe(_cfg(rd, tmp), D1)
        assert set(u["ticker"]) == {"AAA", "BBB"}
        saved = storage.read("universe")
        assert len(saved) == 2 and set(saved["ticker"]) == {"AAA", "BBB"}
        assert pd.to_datetime(saved["date"]).dt.normalize().eq(D1).all()

        # 같은 세션을 다시 부르면 스냅샷을 재사용하고 다시 긁지 않는다
        u2 = rd.resolve_universe(_cfg(rd, tmp), D1)
        assert calls["n"] == 1, f"재실행에서 다시 수집했다 ({calls['n']}회)"
        assert set(u2["ticker"]) == {"AAA", "BBB"}
        assert "date" not in u2.columns, "호출부는 date 열을 기대하지 않는다"
    finally:
        P.sp500_constituents = original                      # type: ignore[assignment]
    print(f"  {D1.date()} 스냅샷 저장 후 재사용 · 수집 호출 {calls['n']}회")


def test_resolve_falls_back_when_fetch_fails(tmp: Path):
    """수집 실패가 파이프라인을 죽이면 안 된다(6장 규약)."""
    rd = _load_run_daily()
    storage.DATA_DIR = tmp                                   # type: ignore[assignment]
    storage.upsert("universe", _snap(D1, ["AAA", "BBB"]), ["date", "ticker"])
    original = P.sp500_constituents
    P.sp500_constituents = lambda: pd.DataFrame()            # type: ignore[assignment]
    try:
        u = rd.resolve_universe(_cfg(rd, tmp), D2)
        assert set(u["ticker"]) == {"AAA", "BBB"}, "직전 스냅샷으로 폴백해야 한다"
        # 폴백은 스냅샷을 새로 쓰지 않는다. 안 받은 걸 받은 것처럼 남기면 안 된다
        saved = storage.read("universe")
        assert set(pd.to_datetime(saved["date"]).dt.normalize()) == {D1}, saved["date"].unique()
    finally:
        P.sp500_constituents = original                      # type: ignore[assignment]
    print("  수집 실패 -> 직전 스냅샷 폴백, 새 스냅샷은 쓰지 않음")


if __name__ == "__main__":
    print("\n[1] as-of 조회")
    test_asof_uses_prior_snapshot()
    print("\n[2] 첫 스냅샷 이전")
    test_asof_before_first_is_empty()
    with tempfile.TemporaryDirectory() as td:
        print("\n[3] 스냅샷 저장·재사용")
        test_resolve_saves_snapshot(Path(td))
    with tempfile.TemporaryDirectory() as td:
        print("\n[4] 수집 실패 폴백")
        test_resolve_falls_back_when_fetch_fails(Path(td))
    print("\n전체 통과")
