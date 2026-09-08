"""세션 freeze 검증 (외부망 없음).

구현은 `src/freeze.py`, 배경은 CLAUDE.md 7장 17번.

이 층이 지켜야 하는 성질은 하나다. **한 번 쓰이면 바뀌지 않는다.**
그래서 "잘 쓰이는가"보다 "다시 쓰려 할 때 확실히 막히는가"를 더 많이 본다.

특히 5번이 이 기능의 존재 이유를 그대로 재현한 것이다. 실측(2026-09-04)에서
같은 세션을 나흘에 걸쳐 네 번 돌리는 동안 신호 종목이 674 -> 754로 늘고
scorecard spread가 38.0178 -> 37.3037bp로 바뀌었다. 그 숫자를 그대로 심어
latest 층이 움직여도 live 층이 최초값을 지키는지 확인한다.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import freeze as FRZ  # noqa: E402

SESSION = pd.Timestamp("2026-09-04")


def _news(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "id": [f"a{i:04d}" for i in range(n)],
        "headline": [f"headline {i}" for i in range(n)],
        "sentiment": [0.1] * n,
        "novelty": [0.9] * n,
        "tickers": [["AAPL"] for _ in range(n)],
    })


def _signals(n: int, date=SESSION, sent: float = 0.20) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [date] * n,
        "ticker": [f"T{i:04d}" for i in range(n)],
        "sent": [sent] * n,
        "sent_w": [sent * 0.9] * n,
        "n_articles": [3] * n,
    })


def _resid(n: int, date=SESSION) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [date] * n,
        "ticker": [f"T{i:04d}" for i in range(n)],
        "resid": [0.001] * n,
    })


def _dirhash(d: Path) -> str:
    """디렉터리 내용 전체의 해시. 불변성 확인용."""
    h = hashlib.sha1()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def test_freeze_creates_snapshot(base: Path):
    assert not FRZ.is_frozen(SESSION, base)
    dest = FRZ.freeze_session(
        SESSION, news=_news(1150), signals=_signals(674), residuals=_resid(490),
        scorecard={"available": True, "spread_bp": 38.0178, "hit": True},
        news_window=(pd.Timestamp("2026-09-03 20:00", tz="UTC"),
                     pd.Timestamp("2026-09-04 20:00", tz="UTC")),
        base=base,
    )
    assert dest.exists()
    assert FRZ.is_frozen(SESSION, base)
    for name in ("news", "signals", "residuals"):
        assert (dest / f"{name}.parquet").exists(), name
    man = FRZ.read_manifest(SESSION, base)
    assert man["session"] == "2026-09-04"
    assert man["rows"] == {"news": 1150, "signals": 674, "residuals": 490}
    assert man["scorecard"]["spread_bp"] == 38.0178
    assert man["provenance"] == "live"
    assert man["schema_version"] == FRZ.SCHEMA_VERSION
    assert man["frozen_at_utc"].endswith("+00:00")
    assert FRZ.list_frozen(base) == ["2026-09-04"]
    print(f"  스냅샷 생성: {man['rows']}, provenance={man['provenance']}")
    return dest


def test_roundtrip(base: Path):
    sig = FRZ.read_frozen(SESSION, "signals", base)
    news = FRZ.read_frozen(SESSION, "news", base)
    assert len(sig) == 674 and len(news) == 1150
    assert set(sig["ticker"]) == set(_signals(674)["ticker"])
    assert FRZ.read_frozen(pd.Timestamp("2026-01-02"), "signals", base).empty
    print(f"  되읽기: signals {len(sig)}행, news {len(news)}행")


def test_refuses_second_write(base: Path, dest: Path):
    """같은 세션에 다시 쓰려 하면 막고, 파일은 한 바이트도 안 바뀐다."""
    before = _dirhash(dest)
    try:
        FRZ.freeze_session(SESSION, news=_news(9), signals=_signals(9),
                           residuals=_resid(9), base=base)
    except FRZ.AlreadyFrozenError as e:
        print(f"  거부됨: {str(e)[:60]}...")
    else:
        raise AssertionError("이미 freeze된 세션에 두 번째 쓰기가 통과했다")
    assert _dirhash(dest) == before, "거부됐는데 내용이 바뀌었다"
    assert FRZ.read_manifest(SESSION, base)["rows"]["signals"] == 674


def test_manifest_gates_is_frozen(base: Path):
    """디렉터리만 있고 manifest가 없으면 freeze가 아니다.

    중간에 죽어 껍데기가 남았을 때 그 세션이 '이미 처리됨'으로 잠기면
    기록이 영구히 빈다. 그래서 판정 기준을 manifest로 잡았다.
    """
    other = pd.Timestamp("2026-09-05")
    FRZ.snapshot_dir(other, base).mkdir(parents=True)
    assert not FRZ.is_frozen(other, base)
    assert FRZ.read_manifest(other, base) is None
    assert other.strftime("%Y-%m-%d") not in FRZ.list_frozen(base)
    # 껍데기가 있어도 정상적으로 쓸 수 있어야 한다
    FRZ.freeze_session(other, news=_news(2), signals=_signals(2, date=other),
                       residuals=_resid(2, date=other), base=base)
    assert FRZ.is_frozen(other, base)
    print("  빈 디렉터리는 freeze로 치지 않는다 (그리고 그 위에 쓸 수 있다)")


def test_live_layer_survives_latest_drift(base: Path):
    """실측 재현: latest 층이 사후에 움직여도 live 층은 최초값을 지킨다.

    2026-09-04 세션의 실제 궤적이다.
      최초    signals 674행, spread 38.0178bp
      나흘 뒤 signals 754행, spread 37.3037bp
    latest 층(storage.upsert)은 뒤엣값으로 덮이는 게 설계상 정상이다.
    live 층은 앞엣값이어야 한다.
    """
    sess = pd.Timestamp("2026-09-08")
    FRZ.freeze_session(sess, news=_news(1150), signals=_signals(674, date=sess, sent=0.20),
                       residuals=_resid(490, date=sess),
                       scorecard={"available": True, "spread_bp": 38.0178},
                       base=base)
    # 나흘 뒤 재실행: 신호가 754행으로 늘고 채점 숫자도 바뀐다
    try:
        FRZ.freeze_session(sess, news=_news(1266), signals=_signals(754, date=sess, sent=0.31),
                           residuals=_resid(493, date=sess),
                           scorecard={"available": True, "spread_bp": 37.3037},
                           base=base)
    except FRZ.AlreadyFrozenError:
        pass
    man = FRZ.read_manifest(sess, base)
    sig = FRZ.read_frozen(sess, "signals", base)
    assert man["rows"]["signals"] == 674, f"live 층이 오염됐다: {man['rows']}"
    assert man["scorecard"]["spread_bp"] == 38.0178
    assert len(sig) == 674 and float(sig["sent"].iloc[0]) == 0.20
    print(f"  latest가 674->754로 움직여도 live는 {man['rows']['signals']}행, "
          f"spread {man['scorecard']['spread_bp']}bp 유지")


def test_session_rows_only(base: Path):
    """다른 날짜 행이 섞여 들어와도 그 세션 행만 남긴다."""
    sess = pd.Timestamp("2026-09-09")
    mixed = pd.concat([_signals(5, date=sess), _signals(7, date=pd.Timestamp("2026-09-08"))])
    FRZ.freeze_session(sess, news=_news(3), signals=mixed,
                       residuals=_resid(4, date=sess), base=base)
    sig = FRZ.read_frozen(sess, "signals", base)
    assert len(sig) == 5, f"세션 밖 행이 섞였다: {len(sig)}"
    assert (pd.to_datetime(sig["date"]).dt.normalize() == sess).all()
    print(f"  혼합 12행 -> 세션 행 {len(sig)}행만 저장")


def test_provenance_validated(base: Path):
    """표시 없는 소급 기록은 실시간 기록을 되돌릴 수 없게 오염시킨다."""
    sess = pd.Timestamp("2026-09-10")
    FRZ.freeze_session(sess, news=_news(1), signals=_signals(1, date=sess),
                       residuals=_resid(1, date=sess), provenance="late", base=base)
    assert FRZ.read_manifest(sess, base)["provenance"] == "late"
    try:
        FRZ.freeze_session(pd.Timestamp("2026-09-11"), news=_news(1),
                           provenance="realtime", base=base)
    except ValueError as e:
        print(f"  provenance 검증: {e}")
    else:
        raise AssertionError("모르는 provenance 값이 통과했다")


def test_no_tmp_left_behind(base: Path):
    """임시 디렉터리가 남으면 다음 실행이 헷갈린다."""
    leftovers = [p.name for p in FRZ.live_root(base).iterdir() if p.name.startswith(".tmp-")]
    assert not leftovers, f"임시 디렉터리가 남았다: {leftovers}"
    print("  임시 디렉터리 잔재 없음")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "live"
        print("\n[1] 스냅샷 생성")
        dest = test_freeze_creates_snapshot(base)
        print("\n[2] 되읽기")
        test_roundtrip(base)
        print("\n[3] 두 번째 쓰기 거부")
        test_refuses_second_write(base, dest)
        print("\n[4] manifest 기준 판정")
        test_manifest_gates_is_frozen(base)
        print("\n[5] latest 표류 중 live 불변 (실측 재현)")
        test_live_layer_survives_latest_drift(base)
        print("\n[6] 세션 행만 저장")
        test_session_rows_only(base)
        print("\n[7] provenance 표시")
        test_provenance_validated(base)
        print("\n[8] 임시 디렉터리 정리")
        test_no_tmp_left_behind(base)
        print("\n전체 통과")
