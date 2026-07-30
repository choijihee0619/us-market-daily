"""조용히 실패한 것들의 회귀 테스트 (외부망 없음).

2026-07-30 첫 실제 실행에서 드러난 세 건. 공통점은 **예외를 던지지 않고**
그럴듯한 리포트를 계속 생산했다는 것이다. 그래서 기존 테스트 4종이 전부
통과하는 상태로 몇 주를 갈 수 있었다.

  1. novelty 자기비교 -> 2번 블록(토픽 회귀)이 통째로 사라진다
  2. parquet 왕복 타입 변화 -> AV 토픽이 폐기되고 LLM 호출을 낭비한다
  3. FRED 공개 지연 -> 1번 블록이 "+0bp"라는 없는 사실을 싣는다

세 번째가 이 프로젝트에서 가장 위험한 종류다. 채점 대상이 되는 기록에
사실이 아닌 숫자가 들어가면 out-of-sample 기록 자체의 신뢰가 깨진다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.collect import macro as M  # noqa: E402
from src.collect import news_alphavantage as AV  # noqa: E402
from src.process import attribution as A  # noqa: E402
from src.process import sentiment as S  # noqa: E402

TMP = Path(__file__).resolve().parents[1] / "out" / "_verify_silent"


def _news(n: int = 6) -> pd.DataFrame:
    """서로 다른 기사 n건. 재탕이 아니므로 novelty가 높아야 정상이다."""
    rows = []
    words = ["earnings beat guidance raised strong demand",
             "profit warning margin pressure weak orders",
             "merger agreement announced premium cash deal",
             "inflation cooled consumer prices eased broadly",
             "chip capex datacenter buildout accelerates sharply",
             "regulator opened probe into pricing practices"]
    for i in range(n):
        rows.append({
            "id": f"id{i:03d}",
            "url": f"https://x.test/{i}",
            "headline": f"Company {i} news: {words[i % len(words)]}",
            "summary": f"Detail {i} " + words[i % len(words)],
            "tickers": [f"T{i%3}"],
            "av_topics": ["실적"] if i % 2 == 0 else ["통화정책"],
            "topic": [],
        })
    return pd.DataFrame(rows)


# ------------------------------------------------- 1. novelty 자기비교

def test_novelty_excludes_self():
    """저장소 전체를 history로 넘겨도 자기 자신과 비교되면 안 된다.

    이 프로젝트의 실제 호출 방식이 그렇다(run_daily.collect):
        hist = storage.read("news"); S.score_dataframe(nw, hist, ...)
    재실행하면 nw의 기사가 이미 hist에 들어 있다.
    """
    df = _news()

    # (a) 첫 실행: history 없음 -> novelty 높음
    first = S.score_dataframe(df, None, 0.85)
    assert first["novelty"].min() > 0.5, first["novelty"].tolist()

    # (b) 재실행: 자기 자신이 들어 있는 history -> 여전히 높아야 한다
    again = S.score_dataframe(df, first, 0.85)
    assert again["novelty"].min() > 0.5, \
        f"자기비교로 novelty가 무너졌다: {again['novelty'].round(3).tolist()}"
    assert abs(float(again["novelty"].mean() - first["novelty"].mean())) < 0.05

    # (c) 진짜 재탕은 여전히 잡아야 한다 (id/url이 다른 동일 내용)
    dup = df.copy()
    dup["id"] = dup["id"] + "_dup"
    dup["url"] = dup["url"] + "?utm=x"
    d = S.score_dataframe(dup, first, 0.85)
    assert d["novelty"].max() < 0.3, \
        f"재탕 탐지가 죽었다 -- 자기 제외를 너무 넓게 잡았다: {d['novelty'].round(3).tolist()}"
    print(f"  첫 실행 novelty 평균 {first['novelty'].mean():.3f} · "
          f"재실행 {again['novelty'].mean():.3f} · 재탕 {d['novelty'].mean():.3f}")


def test_novelty_feeds_topic_matrix():
    """novelty가 0이면 sentiment_w가 0이 되고 토픽 행렬이 전부 0이 된다.

    2번 블록이 사라지는 경로를 끝까지 따라가 확인한다.
    """
    df = S.score_dataframe(_news(), None, 0.85)
    df = AV.seed_topics(df)
    tmat = A.build_topic_matrix(df, ["실적", "통화정책", "M&A"])
    assert not tmat.empty
    nz = (tmat[["실적", "통화정책"]].abs().sum() > 0)
    assert nz.any(), f"토픽 노출이 전부 0이다:\n{tmat}"
    print(f"  토픽 행렬 {tmat.shape}, 값이 있는 열 {int(nz.sum())}개")


# --------------------------------------- 2. parquet 왕복 타입 변화

def test_parquet_roundtrip_keeps_topics():
    """list 열은 parquet에서 numpy 배열로 돌아온다. 그래도 동작해야 한다."""
    TMP.mkdir(parents=True, exist_ok=True)
    p = TMP / "news.parquet"
    df = S.score_dataframe(_news(), None, 0.85)
    df = AV.seed_topics(df)
    df.to_parquet(p, index=False)
    back = pd.read_parquet(p)

    # 왕복 후 실제로 타입이 바뀌는지 먼저 확인 (이 전제가 깨지면 테스트가 무의미)
    assert isinstance(back["topic"].iloc[0], np.ndarray), \
        f"전제 불성립: {type(back['topic'].iloc[0])}"
    assert storage.as_list(back["topic"].iloc[0]) == list(df["topic"].iloc[0])

    # (a) run_daily.classify 의 사전분류 판정
    has = back["topic"].apply(lambda v: len(storage.as_list(v)) > 0)
    assert has.all(), f"사전분류를 {int((~has).sum())}건 놓쳤다 -- LLM 호출 낭비 + 토픽 삭제"

    # (b) seed_topics 재적용이 멱등이어야 한다 (왕복 후에도 폐기하지 않는다)
    reseed = AV.seed_topics(back)
    assert all(len(storage.as_list(v)) > 0 for v in reseed["topic"]), "왕복 후 AV 토픽 폐기"

    # (c) build_topic_matrix 가 죽지 않아야 한다 (--dry-run 경로에서 터졌다)
    tmat = A.build_topic_matrix(back, ["실적", "통화정책"])
    assert not tmat.empty and (tmat[["실적", "통화정책"]].abs().sum() > 0).any()
    print(f"  왕복 후 topic dtype={type(back['topic'].iloc[0]).__name__}, "
          f"사전분류 {int(has.sum())}/{len(back)}건 인식, 토픽 행렬 정상")


def test_as_list_edge_cases():
    assert storage.as_list(None) == []
    assert storage.as_list(np.array([])) == []
    assert storage.as_list(np.array(["a", "b"])) == ["a", "b"]
    assert storage.as_list(["a"]) == ["a"]
    assert storage.as_list(float("nan")) == []
    assert storage.as_list("실적") == ["실적"]        # 문자 단위로 쪼개면 안 된다
    assert storage.as_list(("a", "b")) == ["a", "b"]
    print("  as_list 7개 경계값 통과 (문자열 분해 방지 포함)")


# ------------------------------------------- 3. FRED 공개 지연

def test_macro_staggered_publication():
    """시리즈마다 공개 지연이 달라도 변화량을 조작하지 않아야 한다.

    실측 상황을 그대로 심는다: 세션 07-29에 T10Y2Y만 07-29까지 있고
    DGS10은 07-28까지, DTWEXBGS는 07-24까지.
    """
    idx = pd.to_datetime(["2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29"])
    wide = pd.DataFrame(
        {
            "DGS10":    [4.69, 4.65, 4.61, np.nan],
            "T10Y2Y":   [0.36, 0.34, 0.35, 0.45],
            "DTWEXBGS": [120.91, np.nan, np.nan, np.nan],
            "VIXCLS":   [18.58, 18.67, 18.21, np.nan],
        },
        index=idx,
    )
    out = M.daily_changes(wide, pd.Timestamp("2026-07-29"))

    # DGS10: 07-28 값, 07-27 대비 -4bp. ffill 방식이면 0bp가 나왔다.
    assert out["DGS10"]["stale"] is True
    assert out["DGS10"]["asof"] == pd.Timestamp("2026-07-28")
    assert abs(out["DGS10"]["change"] - (-4.0)) < 1e-6, out["DGS10"]
    assert abs(out["DGS10"]["level"] - 4.61) < 1e-9

    # T10Y2Y: 세션일 값이 있으므로 stale이 아니고 +10bp
    assert out["T10Y2Y"]["stale"] is False
    assert abs(out["T10Y2Y"]["change"] - 10.0) < 1e-6

    # VIXCLS: bp가 아니라 pt 단위
    assert out["VIXCLS"]["unit"] == "pt"
    assert abs(out["VIXCLS"]["change"] - (-0.46)) < 1e-9

    # DTWEXBGS: 관측치가 1개뿐 -> change는 None (0으로 꾸미지 않는다)
    assert out["DTWEXBGS"]["change"] is None, out["DTWEXBGS"]
    assert out["DTWEXBGS"]["asof"] == pd.Timestamp("2026-07-24")
    print("  지연 시리즈 4종: -4bp / +10bp / -0.46pt / None 정확히 복원")
    print("  (ffill 방식이었다면 앞의 셋이 전부 0으로 보고됐다)")


def test_macro_no_fake_zero_in_title():
    """제목·서술이 stale 값을 세션 변화처럼 주장하지 않아야 한다."""
    from src.llm.rule_provider import RuleProvider
    from src.report import builder as B

    ctx = {
        "session": pd.Timestamp("2026-07-29"),
        "benchmarks": {"SPY": -0.0154},
        "macro": {"DGS10": {"level": 4.61, "change": -4.0, "unit": "bp",
                            "asof": pd.Timestamp("2026-07-28"), "stale": True,
                            "prev_asof": pd.Timestamp("2026-07-27")}},
        "cross_section": {"n": 522, "n_up_outlier": 24, "n_down_outlier": 37},
        "topic_regression": {"r2": None, "coef": {}, "n": 0},
    }
    title = B.make_title(ctx)
    assert "10Y" not in title, f"미공개 값을 제목에 넣었다: {title}"
    assert "SPY" in title, title

    narrative = RuleProvider().write_narrative({**ctx, "session": "2026-07-29"})
    assert "마감했다" not in narrative or "미공개" in narrative, narrative
    assert "07-28" in narrative, f"기준일을 밝히지 않았다: {narrative}"
    print(f"  제목: {title}")
    print("  서술에 기준일·미공개 표기 확인")


if __name__ == "__main__":
    print("\n[1] novelty 자기비교 (2번 블록 소실)")
    test_novelty_excludes_self()
    test_novelty_feeds_topic_matrix()
    print("\n[2] parquet 왕복 타입 (AV 토픽 폐기)")
    test_parquet_roundtrip_keeps_topics()
    test_as_list_edge_cases()
    print("\n[3] FRED 공개 지연 (없는 사실 +0bp)")
    test_macro_staggered_publication()
    test_macro_no_fake_zero_in_title()
    print("\n전체 통과")
