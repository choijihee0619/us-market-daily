"""AV 수집 창 검증 (외부망 없음).

## 무엇을 막는 테스트인가

`fetch_news` 는 `time_to` 를 지원하는데 `run_daily.collect` 가 **넘기지 않았다.**
AV는 `sort=LATEST` 라서 `time_to` 가 없으면 *지금까지의* 최신 기사를 돌려주고,
그러면 창보다 나중에 나온 기사가 `limit` 을 채워 정작 창 안 기사가 밀린다.

실측(2026-09-08):
  - 2026-08-10 세션을 그 세션 당일 밤에 돌렸을 때 AV 1,638건을 받았는데
    **창 안은 0건**이었다. 그 세션은 신호가 비어 버려졌다
  - 같은 창을 한 달 뒤 `time_to` 와 함께 1회 호출하니 **1,000건 중 998건이 창 안**

즉 이건 성능 문제가 아니라 데이터 손실이었다. 그리고 조용했다 -- 로그에는
"AV 1638건 수집"만 찍히고 창 안이 0건이라는 말은 어디에도 없었다.

두 번째로, `limit` 도달은 **잘렸다는 신호**다. 특히 월요일 세션은 창이 금요일
마감부터 3일치라 1,000건으로는 부족하다. 조용히 넘어가면 "그날 뉴스가 적었다"로
오독하게 된다.
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collect import news_alphavantage as AV  # noqa: E402

WIN_START = pd.Timestamp("2026-08-07 20:00", tz="UTC")
WIN_END = pd.Timestamp("2026-08-10 20:00", tz="UTC")


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _feed(n: int) -> dict:
    return {"feed": [
        {"title": f"headline {i}", "url": f"https://example.com/{i}",
         "time_published": "20260810T150000",
         "overall_sentiment_score": 0.1, "overall_sentiment_label": "Neutral",
         "ticker_sentiment": [{"ticker": "AAPL", "relevance_score": "0.9"}],
         "topics": [{"topic": "Earnings"}]}
        for i in range(n)
    ]}


def _capture_get(payload, seen: list):
    def fake_get(url, params=None, timeout=None):
        seen.append(dict(params or {}))
        return _Resp(payload)
    return fake_get


def test_fetch_news_sends_time_to():
    seen: list = []
    AV.requests.get = _capture_get(_feed(3), seen)          # type: ignore[attr-defined]
    AV.time.sleep = lambda *_: None                         # type: ignore[attr-defined]
    AV.fetch_news("KEY", time_from=WIN_START, time_to=WIN_END,
                  topic_batches=["earnings"], limit=1000, max_calls=1)
    assert len(seen) == 1
    p = seen[0]
    assert p["time_from"] == "20260807T2000", p
    assert p.get("time_to") == "20260810T2000", f"time_to가 빠졌다: {p}"
    print(f"  time_from={p['time_from']} time_to={p['time_to']}")


def test_fetch_news_without_time_to_is_open_ended():
    """생략하면 열린 구간이다. 그 자체는 정상 동작이지만 호출부가 넘겨야 한다."""
    seen: list = []
    AV.requests.get = _capture_get(_feed(2), seen)          # type: ignore[attr-defined]
    AV.time.sleep = lambda *_: None                         # type: ignore[attr-defined]
    AV.fetch_news("KEY", time_from=WIN_START, topic_batches=["earnings"], max_calls=1)
    assert "time_to" not in seen[0]
    print("  time_to 미지정 시 파라미터에 없음 (열린 구간)")


def test_limit_reached_warns():
    """limit 도달은 잘림이다. 경고가 없으면 커버리지를 오독한다."""
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, r):
            if r.levelno >= logging.WARNING:
                records.append(r.getMessage())

    h = _H()
    AV.log.addHandler(h)
    try:
        AV.requests.get = _capture_get(_feed(50), [])       # type: ignore[attr-defined]
        AV.time.sleep = lambda *_: None                     # type: ignore[attr-defined]
        AV.fetch_news("KEY", time_from=WIN_START, time_to=WIN_END,
                      topic_batches=["earnings"], limit=50, max_calls=1)
    finally:
        AV.log.removeHandler(h)
    assert any("limit" in m for m in records), f"잘림 경고가 없다: {records}"
    print(f"  경고 발생: {records[0][:60]}...")


def test_collect_passes_window_end():
    """배선 확인. fetch_news가 지원해도 run_daily가 안 넘기면 소용없다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rd_wiring", Path(__file__).resolve().parents[1] / "scripts" / "run_daily.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)

    empty = pd.DataFrame()
    rd.P.fetch_prices = lambda *a, **k: empty
    rd.M.fetch_fred = lambda *a, **k: empty
    rd.F.fetch_french_daily = lambda *a, **k: empty
    rd.F.proxy_factors = lambda *a, **k: empty
    rd.F.merge_factors = lambda *a, **k: empty
    rd.N.fetch_rss = lambda *a, **k: empty

    seen: dict = {}

    def fake_av(key, **kw):
        seen.update(kw)
        return empty

    rd.AV.fetch_news = fake_av

    cfg = rd.load_config()
    cfg["news"]["providers"] = ["alphavantage"]
    cfg["news"]["alphavantage"]["enabled"] = True
    session = pd.Timestamp("2026-08-10")
    rd.collect(cfg, session, 30)

    start, end = rd.news_window(session)
    assert seen.get("time_from") == start, f"time_from 불일치: {seen.get('time_from')}"
    assert seen.get("time_to") == end, (
        f"run_daily.collect 가 time_to를 넘기지 않는다: {seen.get('time_to')} (기대 {end})")
    print(f"  collect -> fetch_news(time_from={seen['time_from']}, time_to={seen['time_to']})")


if __name__ == "__main__":
    print("\n[1] time_to 전달")
    test_fetch_news_sends_time_to()
    print("\n[2] time_to 생략 시 열린 구간")
    test_fetch_news_without_time_to_is_open_ended()
    print("\n[3] limit 도달 경고")
    test_limit_reached_warns()
    print("\n[4] run_daily.collect 배선")
    test_collect_passes_window_end()
    print("\n전체 통과")
