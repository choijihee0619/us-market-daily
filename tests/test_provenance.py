"""수집 출처(provenance) 검증 (외부망 없음).

## 무엇을 담는가

기사에 두 개의 시각이 있다. **둘은 다르고, 섞으면 안 된다.**
  - `published_at`     : 기사가 언제 나왔나
  - `collected_at_utc` : 우리가 언제 그걸 알았나

논문 심사에서 "이게 정말 t 시점에 쓸 수 있던 신호인가, 나중에 재구성한 것인가"를
물으면 답하는 건 후자다. 앞엣것은 나중에 수집해도 똑같이 나오므로 증거가 되지 않는다.

## 여기서 특히 조심하는 것

`storage.upsert` 는 기본이 `keep="last"` 다. FRED 잠정치→확정치처럼 **사후 수정을
흡수**하려는 의도된 설계인데, 수집 시각에는 정반대로 작용한다. 같은 기사를 다시
받으면 `collected_at_utc` 가 재수집 시각으로 덮여, 그 기사가 신호 생성 시점에
있었다는 증거가 사라진다. 그래서 `keep_first` 를 만들었고, 이 파일의 3·4번이
그게 실제로 지켜지는지 본다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.collect import news_alphavantage as AV  # noqa: E402

T0 = pd.Timestamp("2026-08-10 21:00", tz="UTC")
T1 = pd.Timestamp("2026-09-08 08:00", tz="UTC")   # 4주 뒤 재수집


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _av_feed(n=3):
    return {"feed": [
        {"title": f"h{i}", "url": f"https://example.com/{i}",
         "time_published": "20260810T150000",
         "overall_sentiment_score": 0.1, "overall_sentiment_label": "Neutral",
         "ticker_sentiment": [{"ticker": "AAPL", "relevance_score": "0.9"}],
         "topics": [{"topic": "Earnings"}]}
        for i in range(n)]}


def test_av_rows_carry_provenance():
    AV.requests.get = lambda *a, **k: _Resp(_av_feed())      # type: ignore[attr-defined]
    AV.time.sleep = lambda *_: None                          # type: ignore[attr-defined]
    df = AV.fetch_news("KEY", time_from=T0, time_to=T0, topic_batches=["earnings"], max_calls=1)
    for col in ("collected_at_utc", "provider", "provider_query"):
        assert col in df.columns, f"{col} 열이 없다"
    assert (df["provider"] == "alphavantage").all()
    assert (df["provider_query"] == "earnings").all()
    assert df["collected_at_utc"].notna().all()
    # published_at 과 다른 값이어야 한다. 같으면 둘 중 하나를 잘못 넣은 것이다
    assert (pd.to_datetime(df["collected_at_utc"], utc=True)
            != pd.to_datetime(df["published_at"], utc=True)).all()
    print(f"  AV {len(df)}행 · provider={df['provider'].iloc[0]} "
          f"· query={df['provider_query'].iloc[0]}")


def _news(ids, collected, sentiment):
    return pd.DataFrame({
        "id": ids,
        "date": [pd.Timestamp("2026-08-10")] * len(ids),
        "headline": [f"h{i}" for i in ids],
        "sentiment": [sentiment] * len(ids),
        "collected_at_utc": [collected] * len(ids),
        "provider": ["alphavantage"] * len(ids),
    })


def test_keep_first_preserves_collection_time(tmp: Path):
    storage.DATA_DIR = tmp                                   # type: ignore[assignment]
    storage.upsert("news", _news(["a", "b"], T0, 0.1), ["id"],
                   keep_first=["collected_at_utc"])
    # 4주 뒤 같은 기사를 다시 받는다. 감성은 갱신되어야 하고 수집 시각은 아니다
    storage.upsert("news", _news(["a", "b"], T1, 0.9), ["id"],
                   keep_first=["collected_at_utc"])
    got = storage.read("news").set_index("id")
    for i in ("a", "b"):
        assert pd.Timestamp(got.loc[i, "collected_at_utc"]) == T0, (
            f"{i}의 수집 시각이 재수집 시각으로 덮였다: {got.loc[i, 'collected_at_utc']}")
        assert got.loc[i, "sentiment"] == 0.9, "다른 열은 최신값이어야 한다"
    print(f"  재수집 후에도 collected_at={T0} 유지 · sentiment는 0.1 -> 0.9 갱신")


def test_keep_first_fills_new_rows(tmp: Path):
    """새 기사는 당연히 이번 수집 시각을 갖는다. 옛 값을 끌어오면 안 된다."""
    storage.DATA_DIR = tmp                                   # type: ignore[assignment]
    storage.upsert("news", _news(["a"], T0, 0.1), ["id"], keep_first=["collected_at_utc"])
    storage.upsert("news", _news(["a", "c"], T1, 0.5), ["id"], keep_first=["collected_at_utc"])
    got = storage.read("news").set_index("id")
    assert pd.Timestamp(got.loc["a", "collected_at_utc"]) == T0
    assert pd.Timestamp(got.loc["c", "collected_at_utc"]) == T1, "새 기사에 옛 시각이 붙었다"
    print("  기존 기사는 T0 유지, 신규 기사는 T1")


def test_keep_first_when_column_absent(tmp: Path):
    """이미 쌓인 3.7만 행에는 이 열이 없다. 그 위에 처음 쓸 때 죽으면 안 된다."""
    storage.DATA_DIR = tmp                                   # type: ignore[assignment]
    legacy = pd.DataFrame({"id": ["a"], "date": [pd.Timestamp("2026-08-10")],
                           "headline": ["h"], "sentiment": [0.1]})
    storage.upsert("news", legacy, ["id"])
    storage.upsert("news", _news(["a", "b"], T1, 0.7), ["id"], keep_first=["collected_at_utc"])
    got = storage.read("news").set_index("id")
    # 옛 행에는 기록이 없었으므로 이번 값이 들어간다. 없는 걸 지어내지는 않는다
    assert pd.Timestamp(got.loc["a", "collected_at_utc"]) == T1
    assert got.loc["a", "sentiment"] == 0.7
    print("  열이 없던 기존 데이터 위에서도 정상 동작")


def test_dictionary_identity():
    """어떤 사전으로 점수를 매겼는지 남지 않으면 시계열을 이어 붙일 수 없다.

    정식 LM 사전과 코드 내장 서브셋은 규모가 25배 다르고 실측상 호환되지 않는다
    (40,407건 기준 상관 0.609, 둘 다 비영인 20,401건 중 부호 반전 10.8%).
    그래서 freeze manifest에 사전 신원을 함께 남긴다.
    """
    from src.process import sentiment as S

    info = S.dictionary_info()
    assert info["source"] in ("lm_master", "builtin_subset"), info
    for k in ("negative", "positive", "uncertainty"):
        assert isinstance(info[k], int) and info[k] > 0, info
    if info["source"] == "lm_master":
        import hashlib
        import json as _json

        meta = Path(__file__).resolve().parents[1] / "data" / "lm_dictionary.meta.json"
        csv = Path(__file__).resolve().parents[1] / "data" / "lm_dictionary.csv"
        assert meta.exists(), "정식 사전을 쓰는데 메타 파일이 없다"
        m = _json.loads(meta.read_text(encoding="utf-8"))
        assert info["sha256"] == m["sha256"]
        if csv.exists():
            got = hashlib.sha256(csv.read_bytes()).hexdigest()
            assert got == m["sha256"], "사전 파일이 메타의 해시와 다르다"
        assert info["negative"] > 1000, f"정식 사전인데 부정 단어가 적다: {info}"
        print(f"  정식 LM 사전 · 부정 {info['negative']} · sha256 {info['sha256'][:12]}...")
    else:
        print(f"  내장 서브셋 (부정 {info['negative']}). "
              f"python scripts/fetch_lm_dictionary.py 로 정식 사전을 받을 것")


if __name__ == "__main__":
    print("\n[1] AV 행의 provenance 열")
    test_av_rows_carry_provenance()
    with tempfile.TemporaryDirectory() as td:
        print("\n[2] 재수집 시 수집 시각 보존")
        test_keep_first_preserves_collection_time(Path(td))
    with tempfile.TemporaryDirectory() as td:
        print("\n[3] 신규 기사는 이번 시각")
        test_keep_first_fills_new_rows(Path(td))
    with tempfile.TemporaryDirectory() as td:
        print("\n[4] 열이 없던 기존 데이터와의 호환")
        test_keep_first_when_column_absent(Path(td))
    print("\n[5] 감성 사전 신원")
    test_dictionary_identity()
    print("\n전체 통과")
