#!/usr/bin/env python3
"""latest 층의 감성 점수를 현재 사전으로 다시 매긴다. live 층은 건드리지 않는다.

## 왜 하는가

2026-09-08에 정식 Loughran-McDonald 사전을 넣기 전까지 점수는 코드 내장 축약
서브셋으로 매겨졌다. 두 척도는 호환되지 않는다 -- 40,407건 기준 상관 0.609,
둘 다 비영인 20,401건 중 부호 반전 10.8%. 그대로 두면 **기록 한가운데에 측정
도구가 바뀐 이음매**가 생긴다. 그 이음매는 시장에서 온 것이 아니라 우리가 만든
것이라, 2단계 분석(Fama-MacBeth, event study)에서 구조변화로 오독될 수 있다.

지금이 가장 싸다. 24세션 4만 행이고, 60거래일을 채운 뒤에는 몇 배가 된다.

## 무엇을 다시 매기고 무엇을 두는가

  다시 매김  sentiment, n_pos, n_neg, n_unc  (텍스트만의 결정적 함수)
             sentiment_w = sentiment x w(novelty)
             signals    (세션별 종목 집계 = 위 값의 함수)
             scorecard  (전일 신호 대 당일 잔차)
  그대로 둠  novelty

**novelty를 다시 계산하지 않는 이유가 중요하다.** novelty는 과거 헤드라인과의
비교라서 이력 의존적이다. 지금 저장소 전체를 놓고 일괄 계산하면 그 세션 시점에
존재하지 않던 미래 기사와 비교하게 된다 -- look-ahead다. 그 세션 시점의 이력을
정확히 재현하려면 세션별로 잘라 순차 계산해야 하는데, 그건 재스코어링이 아니라
재수집에 가깝다. 감성은 텍스트만의 함수라 그런 위험이 없다.

## live 층은 절대 건드리지 않는다

`data/live/` 는 그 시점에 실제로 무엇을 알았나의 기록이고, manifest에 어떤 사전을
썼는지 이미 적혀 있다. 재스코어링은 **ex-post 층에만** 적용한다. 그래서 나중에
`Signal^live`(그때 사전) 대 `Signal^revised`(지금 사전)를 비교할 수 있다.

사용:
    python scripts/rescore_history.py --dry-run   # 바뀌는 양만 보고 쓰지 않는다
    python scripts/rescore_history.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.calendar_utils import news_window, previous_session  # noqa: E402
from src.collect import news as N  # noqa: E402
from src.config import DATA_DIR, load_config  # noqa: E402
from src.process import attribution as A  # noqa: E402
from src.process import sentiment as S  # noqa: E402

META_PATH = DATA_DIR / "rescore.meta.json"


def _rescore_news(news: pd.DataFrame, thr: float) -> tuple[pd.DataFrame, dict]:
    text = news["headline"].fillna("") + ". " + news.get("summary", pd.Series([""] * len(news))).fillna("")
    scores = pd.DataFrame([S.score_text(t) for t in text], index=news.index)

    out = news.copy()
    before = pd.to_numeric(out.get("sentiment"), errors="coerce")
    out["sentiment"] = scores["tone"]
    out["n_pos"], out["n_neg"], out["n_unc"] = scores["pos"], scores["neg"], scores["unc"]

    nov = pd.to_numeric(out.get("novelty"), errors="coerce").fillna(0.0)
    w = np.where(nov >= (1 - thr), nov, nov * 0.3)
    out["sentiment_w"] = out["sentiment"] * w
    out["scored_with"] = S.dictionary_tag()

    after = out["sentiment"]
    both = before.notna() & after.notna()
    nz = both & (before != 0) & (after != 0)
    delta = {
        "rows": int(len(out)),
        "changed": int((both & (before != after)).sum()),
        "corr": float(before[both].corr(after[both])) if both.sum() > 1 else None,
        "sign_flips": int((np.sign(before[nz]) != np.sign(after[nz])).sum()),
        "sign_flip_base": int(nz.sum()),
        "mean_before": float(before[both].mean()) if both.any() else None,
        "mean_after": float(after[both].mean()) if both.any() else None,
    }
    return out, delta


def _rebuild_signals(news: pd.DataFrame, sessions: list[pd.Timestamp]) -> pd.DataFrame:
    frames = []
    for s in sessions:
        start, end = news_window(s)
        win = N.filter_window(news, start, end)
        if win.empty:
            continue
        sig = S.aggregate_by_ticker(win)
        if sig.empty:
            continue
        sig["date"] = s
        frames.append(sig)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _rebuild_scorecard(signals: pd.DataFrame, resid: pd.DataFrame,
                       sessions: list[pd.Timestamp]) -> tuple[pd.DataFrame, list[dict]]:
    sd = pd.to_datetime(signals["date"]).dt.normalize() if not signals.empty else pd.Series(dtype="datetime64[ns]")
    rd = pd.to_datetime(resid["date"]).dt.normalize()
    rows, diffs = [], []
    old = storage.read("scorecard")
    od = pd.to_datetime(old["date"]).dt.normalize() if not old.empty else pd.Series(dtype="datetime64[ns]")

    for s in sessions:
        try:
            prev = previous_session(s)
        except ValueError:
            continue
        prev_sig = signals[sd == prev] if not signals.empty else pd.DataFrame()
        day_res = resid[rd == s]
        if prev_sig.empty or day_res.empty:
            continue
        sc = A.scorecard(prev_sig, day_res)
        if not sc.get("available"):
            continue
        rows.append({**sc, "date": s})
        if not old.empty and (od == s).any():
            o = old[od == s].iloc[0]
            diffs.append({
                "session": str(s.date()),
                "spread_before": float(o.get("spread_bp", float("nan"))),
                "spread_after": float(sc["spread_bp"]),
                "hit_before": bool(o.get("hit", False)),
                "hit_after": bool(sc["hit"]),
            })
    return pd.DataFrame(rows), diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    thr = float(cfg.get_path("sentiment.novelty_threshold", 0.85))
    info = S.dictionary_info()
    print(f"현재 사전: {S.dictionary_tag()} "
          f"(부정 {info['negative']} · 긍정 {info['positive']} · 불확실 {info['uncertainty']})")
    if info["source"] != "lm_master":
        print("정식 사전이 아니다. 먼저 python scripts/fetch_lm_dictionary.py 를 돌릴 것.")
        return 1

    news = storage.read("news")
    resid = storage.read("residuals")
    if news.empty or resid.empty:
        print("news 또는 residuals 가 비어 있다.")
        return 1

    sessions = sorted(pd.to_datetime(resid["date"]).dt.normalize().unique())
    print(f"뉴스 {len(news):,}행 · 잔차 보유 세션 {len(sessions)}개 "
          f"({sessions[0].date()} ~ {sessions[-1].date()})")

    news2, d = _rescore_news(news, thr)
    print()
    print("[감성 재계산]")
    print(f"  값이 바뀐 행    {d['changed']:,} / {d['rows']:,} ({d['changed']/d['rows']*100:.1f}%)")
    print(f"  이전-이후 상관  {d['corr']:.3f}")
    print(f"  부호 반전       {d['sign_flips']:,} / {d['sign_flip_base']:,} "
          f"({d['sign_flips']/max(d['sign_flip_base'],1)*100:.1f}%)")
    print(f"  평균 tone       {d['mean_before']:+.4f} -> {d['mean_after']:+.4f}")

    signals2 = _rebuild_signals(news2, sessions)
    old_sig = storage.read("signals")
    print()
    print("[신호 재집계]")
    print(f"  {len(old_sig):,}행 -> {len(signals2):,}행 "
          f"(세션 {signals2['date'].nunique() if not signals2.empty else 0}개)")

    sc2, diffs = _rebuild_scorecard(signals2, resid, sessions)
    print()
    print("[채점 재계산]")
    print(f"  {len(storage.read('scorecard')):,}행 -> {len(sc2):,}행")
    flips = [x for x in diffs if x["hit_before"] != x["hit_after"]]
    if diffs:
        arr = np.array([abs(x["spread_after"] - x["spread_before"]) for x in diffs])
        print(f"  spread 변화 중위 {np.median(arr):.2f}bp · 최대 {arr.max():.2f}bp")
        print(f"  hit 판정이 뒤집힌 세션 {len(flips)}개"
              + (": " + ", ".join(x["session"] for x in flips) if flips else ""))

    if args.dry_run:
        print("\n--dry-run 이라 아무것도 쓰지 않았다.")
        return 0

    storage.upsert("news", news2, ["id"], keep_first=["collected_at_utc"])
    if not signals2.empty:
        storage.upsert("signals", signals2, ["date", "ticker"])
    if not sc2.empty:
        storage.upsert("scorecard", sc2, ["date"])

    META_PATH.write_text(json.dumps({
        "rescored_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dictionary": S.dictionary_tag(),
        "dictionary_info": info,
        "sentiment_delta": d,
        "sessions": [str(s.date()) for s in sessions],
        "scorecard_hit_flips": flips,
        "scope": ("latest 층만. data/live/ 스냅샷은 건드리지 않는다. "
                  "novelty는 이력 의존이라 다시 계산하지 않았다(look-ahead 위험)."),
    }, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    print(f"\n기록: {META_PATH}")
    print("data/live/ 는 건드리지 않았다. 각 스냅샷은 그 시점 사전으로 매긴 값을 유지한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
