"""감성 스코어링 baseline: Loughran-McDonald 금융 사전.

왜 범용 사전(Harvard IV 등)을 쓰면 안 되는가:
Loughran & McDonald(2011)는 Harvard IV의 '부정' 단어 중 약 4분의 3이 금융 문맥에서
부정이 아님을 보였다. liability, tax, cost, capital, vice(=vice president),
depreciation 같은 단어가 전부 부정으로 잡힌다. 그래서 금융 텍스트에는 LM 사전을 쓴다.

여기 내장된 어휘는 LM 사전의 축약 서브셋이다. 정식 사전(약 4,000단어)은
https://sraf.nd.edu/loughranmcdonald-master-dictionary/ 에서 받아
data/lm_dictionary.csv 로 두면 자동으로 우선 사용한다.
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import DATA_DIR

log = logging.getLogger(__name__)

_NEG = """
decline declines declined declining drop drops dropped fall falls fell falling
loss losses losing weak weakness weaker worsen worsened deteriorate deteriorating
miss misses missed shortfall downgrade downgraded cut cuts cutting slash slashed
layoff layoffs recession slowdown slowing contraction default defaults bankruptcy
lawsuit lawsuits probe investigation fraud penalty fine fined sanction sanctions
warn warns warned warning risk risks risky volatile volatility uncertainty
concern concerns concerned fear fears plunge plunged plunges slump slumped
tumble tumbled sink sank crash crashed selloff bearish halt halted recall
delay delayed suspend suspended breach outage shutdown strike disruption
""".split()

_POS = """
gain gains gained rise rises rose rising surge surged surges jump jumped
rally rallied strong stronger strength improve improved improving beat beats
exceeded outperform outperformed upgrade upgraded raise raised boost boosted
record profit profits profitable growth grow growing expand expansion
approval approved breakthrough milestone partnership win wins won award
recovery rebound rebounded optimistic bullish momentum accelerate accelerated
demand robust resilient efficiency dividend buyback
""".split()

_UNCERTAIN = """
may might could possibly perhaps uncertain uncertainty approximate roughly
tentative preliminary depends unclear ambiguous potential
""".split()

# 부정어 처리: 'not strong', 'no growth' 는 극성을 뒤집는다
_NEGATORS = {"not", "no", "never", "none", "cannot", "without", "fails", "failed", "unable"}

_TOKEN = re.compile(r"[a-z']+")


def _load_lm_dictionary() -> tuple[set[str], set[str], set[str]] | None:
    """정식 LM 마스터 사전이 있으면 로드."""
    p = DATA_DIR / "lm_dictionary.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
        cols = {c.lower(): c for c in df.columns}
        word_c = cols.get("word")
        if not word_c:
            return None
        words = df[word_c].astype(str).str.lower()
        neg = set(words[df[cols["negative"]] > 0]) if "negative" in cols else set()
        pos = set(words[df[cols["positive"]] > 0]) if "positive" in cols else set()
        unc = set(words[df[cols["uncertainty"]] > 0]) if "uncertainty" in cols else set()
        log.info("LM 정식 사전 로드: neg=%d pos=%d unc=%d", len(neg), len(pos), len(unc))
        return neg, pos, unc
    except Exception as e:
        log.warning("LM 사전 로드 실패, 내장 서브셋 사용: %s", e)
        return None


_loaded = _load_lm_dictionary()
NEG, POS, UNC = _loaded if _loaded else (set(_NEG), set(_POS), set(_UNCERTAIN))


def score_text(text: str) -> dict[str, float]:
    """정규화 감성 점수. tone = (pos - neg) / sqrt(n_tokens)

    단순 (pos-neg)/n 이 아니라 sqrt로 나누는 이유: 짧은 헤드라인이 극단값을 갖는
    문제를 완화하기 위함. 헤드라인은 보통 8~15 토큰이라 분모가 작다.
    """
    toks = _TOKEN.findall((text or "").lower())
    if not toks:
        return {"pos": 0.0, "neg": 0.0, "unc": 0.0, "tone": 0.0, "n_tokens": 0}

    pos = neg = unc = 0
    for i, t in enumerate(toks):
        flip = i > 0 and toks[i - 1] in _NEGATORS
        if t in POS:
            neg += 1 if flip else 0
            pos += 0 if flip else 1
        elif t in NEG:
            pos += 1 if flip else 0
            neg += 0 if flip else 1
        if t in UNC:
            unc += 1

    n = len(toks)
    return {
        "pos": pos, "neg": neg, "unc": unc,
        "tone": (pos - neg) / math.sqrt(n),
        "n_tokens": n,
    }


def _shingles(text: str, k: int = 4) -> set[str]:
    toks = _TOKEN.findall((text or "").lower())
    if len(toks) < k:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def compute_novelty(df: pd.DataFrame, history: pd.DataFrame | None = None) -> pd.Series:
    """재탕 기사 탐지. 과거 헤드라인들과의 최대 Jaccard 유사도의 여집합.

    novelty = 1 - max_jaccard.  1에 가까울수록 새로운 뉴스.
    임베딩 코사인이 더 정확하지만 의존성이 무겁다. 4-gram shingle Jaccard는
    같은 사건의 재작성 기사를 잡는 데 실무적으로 충분히 동작한다.  [검증 필요]
    """
    if df.empty:
        return pd.Series(dtype=float)

    texts = (df["headline"].fillna("") + " " + df.get("summary", "").fillna("")).tolist()
    hist_sets: list[set[str]] = []
    if history is not None and not history.empty:
        hist = history
        # **자기 자신을 먼저 뺀다.** history로 저장소 전체를 넘기는데, 재실행이나
        # 중복 수집으로 같은 기사가 이미 저장되어 있으면 자기 자신과 비교되어
        # Jaccard=1 -> novelty=0 이 된다. 첫 실행만 정상이고 이후 모든 실행에서
        # novelty가 0으로 깔린다(2026-07-30 실측: 1348건 중 1276건이 정확히 0).
        # 그러면 sentiment_w = sentiment x novelty 가 0이 되고, 토픽 노출 행렬이
        # 전부 0이 되어 **2번 블록의 토픽 회귀가 통째로 사라진다.** 조용히.
        for key in ("id", "url"):
            if key in hist.columns and key in df.columns:
                own = set(df[key].dropna())
                if own:
                    hist = hist[~hist[key].isin(own)]
        if not hist.empty:
            ht = (hist["headline"].fillna("") + " " + hist.get("summary", "").fillna("")).tolist()
            hist_sets = [_shingles(t) for t in ht[-3000:]]

    out = []
    seen: list[set[str]] = list(hist_sets)
    for t in texts:
        s = _shingles(t)
        best = 0.0
        if s:
            for prev in seen:
                if not prev:
                    continue
                inter = len(s & prev)
                if inter == 0:
                    continue
                j = inter / len(s | prev)
                if j > best:
                    best = j
                    if best > 0.95:
                        break
        out.append(1.0 - best)
        seen.append(s)
    return pd.Series(out, index=df.index)


def score_dataframe(df: pd.DataFrame, history: pd.DataFrame | None = None,
                    novelty_threshold: float = 0.85) -> pd.DataFrame:
    """뉴스 DF에 감성·novelty·가중감성을 붙인다."""
    if df.empty:
        return df
    df = df.copy()
    text = df["headline"].fillna("") + ". " + df.get("summary", "").fillna("")
    scores = pd.DataFrame([score_text(t) for t in text], index=df.index)
    df["sentiment"] = scores["tone"]
    df["n_pos"], df["n_neg"], df["n_unc"] = scores["pos"], scores["neg"], scores["unc"]
    df["novelty"] = compute_novelty(df, history)
    # 재탕 기사는 가중치를 낮춘다. 그러지 않으면 같은 뉴스의 반복이 감성을 뻥튀기한다.
    w = np.where(df["novelty"] >= (1 - novelty_threshold), df["novelty"], df["novelty"] * 0.3)
    df["sentiment_w"] = df["sentiment"] * w
    return df


def aggregate_by_ticker(news: pd.DataFrame) -> pd.DataFrame:
    """종목별 일간 감성 집계. attention(기사수)은 감성과 별개 효과라 함께 남긴다."""
    if news.empty or "tickers" not in news.columns:
        return pd.DataFrame(columns=["date", "ticker", "sent", "sent_w", "n_articles", "novelty_mean"])

    ex = news.explode("tickers").rename(columns={"tickers": "ticker"})
    ex = ex[ex["ticker"].notna() & (ex["ticker"] != "")]
    if ex.empty:
        return pd.DataFrame(columns=["date", "ticker", "sent", "sent_w", "n_articles", "novelty_mean"])

    g = ex.groupby(["date", "ticker"]).agg(
        sent=("sentiment", "mean"),
        sent_w=("sentiment_w", "mean"),
        n_articles=("id", "count"),
        novelty_mean=("novelty", "mean"),
    ).reset_index()
    return g
