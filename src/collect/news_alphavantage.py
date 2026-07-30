"""Alpha Vantage NEWS_SENTIMENT 수집기.

RSS 대비 결정적 장점: **엔티티 태깅이 내장되어 있다.**
`ticker_sentiment` 배열에 종목별 relevance_score와 sentiment_score가 들어 있어서,
회사명 부분일치와 티커-일반단어 충돌(A, ALL, CAT, KEY, ON) 오탐 문제가 사라진다.
이게 지금 파이프라인의 가장 큰 데이터 품질 병목이었다.

**무료 티어 제약이 심하다: 하루 25요청, 분당 5요청.**
그래서 종목별로 부르면 안 된다. 한 번 호출에 limit=1000까지 받을 수 있으므로
토픽 단위로 3~5회만 부르는 설계로 간다. 예산을 config에서 통제한다.

주의: AV 자체 감성 점수(overall_sentiment_score)는 산출 방식이 공개되어 있지 않다.
Loughran-McDonald 사전 점수와 **함께** 저장하되, 주 분석은 재현 가능한 LM 쪽을
쓰고 AV 점수는 비교용 보조 지표로 둔다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Iterable

import pandas as pd
import requests

from ..storage import as_list

log = logging.getLogger(__name__)

BASE = "https://www.alphavantage.co/query"

# AV가 지원하는 토픽 슬러그 -> 우리 토픽 체계
# AV 토픽에는 **뉴스 주제와 산업 섹터가 섞여 있다.** 이걸 구분하지 않고 전부
# 우리 토픽으로 매핑했더니 오분류가 대량 발생했다(2026-07-30 실측).
#   financial_markets -> 통화정책  : 금융시장 일반 기사가 전부 통화정책이 됐다.
#                                    Garmin 실적 기사가 '통화정책' 대표 헤드라인으로 올라왔다.
#   technology/blockchain -> AI투자 : 기술 섹터 기사 ≠ AI 설비투자 뉴스
#   finance/life_sciences/real_estate -> 실적 : 섹터를 주제로 바꿔치기한 것이다
#   economy_macro -> 인플레이션     : macro는 GDP·고용·물가를 모두 포함한다
#
# 그래서 **의미가 정확히 일치하는 3개만 남긴다.** 나머지는 LLM 분류로 보낸다.
# 섹터 토픽 배치를 계속 요청하는 것과는 별개다 -- 그건 종목 커버리지를 위한 것이고
# (7장 5번), 토픽 라벨로 쓰지 않을 뿐이다.
AV_TOPICS = {
    "economy_monetary": "통화정책",
    "earnings": "실적",
    "mergers_and_acquisitions": "M&A",
}

# 무료 티어 25/day 안에서 돌리기 위한 기본 배치
DEFAULT_BATCHES = [
    "economy_monetary,economy_macro",
    "earnings",
    "technology,blockchain",
    "mergers_and_acquisitions,economy_fiscal",
]


def _mk_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode("utf-8")).hexdigest()[:16]


def _parse_ts(s: str) -> pd.Timestamp | None:
    """AV 형식: 20260724T203000 (UTC)."""
    try:
        return pd.Timestamp(pd.to_datetime(s, format="%Y%m%dT%H%M%S"), tz="UTC")
    except Exception:
        return None


def fetch_news(
    api_key: str,
    time_from: pd.Timestamp,
    time_to: pd.Timestamp | None = None,
    topic_batches: Iterable[str] | None = None,
    limit: int = 1000,
    relevance_min: float = 0.25,
    max_calls: int = 5,
) -> pd.DataFrame:
    """NEWS_SENTIMENT 수집.

    relevance_min: ticker_sentiment의 relevance_score 하한. AV는 스쳐 지나간 언급도
    낮은 relevance로 붙여주므로 걸러야 한다. 0.25는 보수적 기본값이며 실측 후
    조정할 것.  [검증 필요]
    """
    if not api_key:
        log.warning("ALPHAVANTAGE_API_KEY 없음 -- AV 수집 건너뜀")
        return pd.DataFrame()

    batches = list(topic_batches or DEFAULT_BATCHES)[:max_calls]
    rows: list[dict] = []
    calls = 0

    for topics in batches:
        params = {
            "function": "NEWS_SENTIMENT",
            "topics": topics,
            "time_from": pd.Timestamp(time_from).tz_convert("UTC").strftime("%Y%m%dT%H%M"),
            "limit": str(limit),
            "sort": "LATEST",
            "apikey": api_key,
        }
        if time_to is not None:
            params["time_to"] = pd.Timestamp(time_to).tz_convert("UTC").strftime("%Y%m%dT%H%M")

        try:
            r = requests.get(BASE, params=params, timeout=40)
            r.raise_for_status()
            js = r.json()
        except Exception as e:
            log.warning("AV 호출 실패 (%s): %s", topics, e)
            continue
        calls += 1

        # AV는 한도 초과나 오류를 200 + 메시지 본문으로 돌려준다
        if "Information" in js or "Note" in js:
            log.warning("AV 한도/안내 메시지: %s",
                        str(js.get("Information") or js.get("Note"))[:200])
            break
        if "Error Message" in js:
            log.warning("AV 오류: %s", js["Error Message"])
            continue

        feed = js.get("feed", [])
        log.info("AV %s -> %d건 (호출 %d/%d)", topics, len(feed), calls, len(batches))

        for item in feed:
            published = _parse_ts(item.get("time_published", ""))
            if published is None:
                continue
            url = item.get("url", "")
            title = (item.get("title") or "").strip()
            if not title:
                continue

            tickers = [
                t.get("ticker") for t in item.get("ticker_sentiment", [])
                if t.get("ticker") and float(t.get("relevance_score", 0) or 0) >= relevance_min
            ]
            # dict 그대로 두면 parquet 저장 시 스키마가 매일 바뀌어 깨진다.
            # JSON 문자열로 직렬화한다.
            rel = json.dumps({
                t.get("ticker"): round(float(t.get("relevance_score", 0) or 0), 4)
                for t in item.get("ticker_sentiment", [])
                if t.get("ticker")
            }, ensure_ascii=False)
            # 원시 슬러그를 함께 저장한다. 매핑만 저장하면 나중에 매핑을 고쳐도
            # 과거 데이터를 다시 만들 수 없어 재수집이 강제된다(2026-07-30에 실제로
            # 겪었다). 원시값이 있으면 재매핑이 오프라인으로 끝난다.
            raw_topics = [str(t.get("topic")) for t in item.get("topics", []) if t.get("topic")]
            av_topics = [AV_TOPICS[t] for t in raw_topics if t in AV_TOPICS]

            rows.append({
                "id": _mk_id(url, title),
                "published_at": published,
                "date": published.tz_convert("America/New_York").normalize().tz_localize(None),
                "source": f"AV/{item.get('source','')}",
                "headline": title,
                "summary": (item.get("summary") or "")[:600],
                "url": url,
                "tickers": sorted(set(tickers)),
                "av_sentiment": float(item.get("overall_sentiment_score", 0) or 0),
                "av_label": item.get("overall_sentiment_label", ""),
                "av_relevance": rel,
                "av_topics": sorted(set(av_topics)),
                "av_topics_raw": sorted(set(raw_topics)),
            })

        time.sleep(13)  # 분당 5요청 제한 -> 12초 이상 간격

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).drop_duplicates(subset=["id"])
    log.info("AV 수집 완료: %d건 (고유), 호출 %d회", len(df), calls)
    return df


def merge_with_rss(av: pd.DataFrame, rss: pd.DataFrame) -> pd.DataFrame:
    """AV와 RSS를 합친다. 겹치는 기사는 AV 쪽(태깅이 있는 쪽)을 남긴다.

    URL 해시가 다르더라도 같은 사건일 수 있으나, 그건 novelty 계산이 잡아낸다.
    여기서는 동일 URL 중복만 제거한다.
    """
    if av.empty:
        return rss
    if rss.empty:
        return av

    # AV에 이미 있는 URL은 RSS에서 뺀다
    av_urls = set(av["url"].dropna())
    rss_f = rss[~rss["url"].isin(av_urls)].copy()

    # 전부 NA인 열이 섞이면 concat dtype 경고가 난다. 명시적 dtype으로 채운다.
    defaults = {"av_sentiment": float("nan"), "av_label": "", "av_relevance": "{}"}
    for col, dv in defaults.items():
        if col not in rss_f.columns:
            rss_f[col] = dv
    for col in ("av_topics", "av_topics_raw"):
        if col not in rss_f.columns:
            rss_f[col] = [[] for _ in range(len(rss_f))]

    common = [c for c in av.columns if c in rss_f.columns]
    extra_rss = [c for c in rss_f.columns if c not in common]
    extra_av = [c for c in av.columns if c not in common]
    cols = common + extra_av + extra_rss
    return pd.concat([av.reindex(columns=cols), rss_f.reindex(columns=cols)],
                     ignore_index=True)


def seed_topics(df: pd.DataFrame) -> pd.DataFrame:
    """AV 토픽을 우리 topic 열의 초기값으로 쓴다.

    LLM 분류 호출을 그만큼 줄일 수 있다. LLM이 나중에 덮어쓴다.
    """
    if df.empty:
        return df
    df = df.copy()
    if "topic" not in df.columns:
        df["topic"] = [[] for _ in range(len(df))]
    if "av_topics" in df.columns:
        # isinstance 검사로는 안 된다. parquet 왕복 후 numpy 배열로 돌아오면
        # 조용히 False가 되어 AV 토픽이 전량 폐기된다.
        df["topic"] = [
            as_list(av) if len(as_list(av)) > 0 else as_list(t)
            for av, t in zip(df["av_topics"], df["topic"])
        ]
    return df


def remap_topics(df: pd.DataFrame) -> pd.DataFrame:
    """저장된 av_topics_raw로 av_topics·topic을 다시 계산한다.

    AV_TOPICS 매핑을 고쳤을 때 재수집 없이 과거 데이터를 갱신하는 경로다.
    raw가 없는 과거 행은 손대지 않는다.
    """
    if df.empty or "av_topics_raw" not in df.columns:
        return df
    df = df.copy()
    has_raw = df["av_topics_raw"].apply(lambda v: len(as_list(v)) > 0)
    if not has_raw.any():
        return df
    new = df.loc[has_raw, "av_topics_raw"].apply(
        lambda v: sorted({AV_TOPICS[t] for t in as_list(v) if t in AV_TOPICS}))
    df.loc[has_raw, "av_topics"] = new
    df.loc[has_raw, "topic"] = new
    return df
