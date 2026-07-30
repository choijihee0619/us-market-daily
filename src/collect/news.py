"""뉴스 수집: RSS + SEC EDGAR.

원칙:
- 전문(full text)은 저장하지 않는다. 헤드라인·요약·링크까지만. (저작권/ToS)
- published_at은 UTC 초 단위로 저장한다. 뉴스-수익률 연구에서 결과를 망치는
  1순위 원인이 타임스탬프 정렬 실패다.
- id는 URL 해시로 만들어 재실행 시 중복 적재를 막는다.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Iterable

import pandas as pd
import requests

log = logging.getLogger(__name__)

NEWS_COLS = [
    "id", "published_at", "date", "source", "headline", "summary", "url",
    "tickers", "topic", "sentiment", "novelty",
]


def _mk_id(url: str, headline: str) -> str:
    return hashlib.sha1(f"{url}|{headline}".encode("utf-8")).hexdigest()[:16]


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# 정부기관 피드는 일반 UA를 막는다. BLS는 Mozilla/5.0에 403을 주고 연락처가 든
# UA에는 200을 준다(2026-07-30 실측). SEC EDGAR와 같은 요구다.
DEFAULT_RSS_UA = "us-market-daily (research)"


def fetch_rss(feeds: Iterable[dict], limit_per_feed: int = 120,
              user_agent: str | None = None) -> pd.DataFrame:
    """RSS 수집.

    feedparser.parse(url)에 URL을 직접 주면 feedparser가 내부적으로 urllib으로
    받는다. macOS framework Python은 시스템 인증서를 참조하지 않아 전 피드가
    URLError로 죽는다(2026-07-30 실측: 6/6 실패). requests는 certifi를 쓰므로
    받기와 파싱을 분리한다. 덤으로 HTTP 상태코드가 보여서 '피드가 비었다'와
    '차단당했다'를 구분할 수 있게 된다 -- 원래는 둘 다 같은 경고로 뭉쳐졌다.
    """
    import feedparser

    ua = user_agent or DEFAULT_RSS_UA
    rows: list[dict[str, Any]] = []
    for feed in feeds:
        name, url = feed.get("name", "rss"), feed.get("url")
        if not url:
            continue
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=25)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
        except Exception as e:
            log.warning("RSS %s 수집 실패: %s", name, e)
            continue
        if not parsed.entries:
            log.warning("RSS %s 항목 0건 (피드가 비었거나 구조가 바뀌었다)", name)
            continue

        for e in parsed.entries[:limit_per_feed]:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            if not ts:
                continue
            published = pd.Timestamp(time.strftime("%Y-%m-%dT%H:%M:%S", ts), tz="UTC")
            link = e.get("link", "")
            headline = _clean(e.get("title"))
            if not headline:
                continue
            rows.append(
                {
                    "id": _mk_id(link, headline),
                    "published_at": published,
                    "date": published.tz_convert("America/New_York").normalize().tz_localize(None),
                    "source": name,
                    "headline": headline,
                    "summary": _clean(e.get("summary"))[:600],
                    "url": link,
                }
            )
        time.sleep(0.2)

    if not rows:
        return pd.DataFrame(columns=NEWS_COLS)
    return pd.DataFrame(rows).drop_duplicates(subset=["id"])


def fetch_edgar(forms: Iterable[str], user_agent: str, count: int = 100) -> pd.DataFrame:
    """EDGAR 최근 공시. SEC는 연락처가 담긴 User-Agent를 요구하며 미기재 시 403."""
    rows: list[dict[str, Any]] = []
    for form in forms:
        url = (
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
            f"&type={form}&company=&dateb=&owner=include&count={count}&output=atom"
        )
        try:
            r = requests.get(url, headers={"User-Agent": user_agent}, timeout=25)
            r.raise_for_status()
        except Exception as e:
            log.warning("EDGAR %s 실패: %s", form, e)
            continue

        import feedparser

        parsed = feedparser.parse(r.text)
        for e in parsed.entries:
            ts = e.get("updated_parsed") or e.get("published_parsed")
            if not ts:
                continue
            published = pd.Timestamp(time.strftime("%Y-%m-%dT%H:%M:%S", ts), tz="UTC")
            title = _clean(e.get("title"))
            link = e.get("link", "")
            rows.append(
                {
                    "id": _mk_id(link, title),
                    "published_at": published,
                    "date": published.tz_convert("America/New_York").normalize().tz_localize(None),
                    "source": f"SEC {form}",
                    "headline": title,
                    "summary": _clean(e.get("summary"))[:400],
                    "url": link,
                }
            )
        time.sleep(0.35)  # SEC 권고 10 req/s 보다 훨씬 보수적으로

    if not rows:
        return pd.DataFrame(columns=NEWS_COLS)
    return pd.DataFrame(rows).drop_duplicates(subset=["id"])


def tag_tickers(df: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """헤드라인·요약에서 종목 태깅.

    한계가 분명하다: 회사명 부분일치는 오탐이 많고(예: 'Apple' vs 'Apple Hospitality'),
    티커 대문자 매칭은 일반 단어와 충돌한다(A, ALL, CAT, KEY, IT, ON...).
    그래서 (1) 3글자 이상 티커만 단어경계로 매칭, (2) 정식 회사명 매칭을 우선한다.
    정밀 태깅이 필요하면 유료 뉴스 API의 entity 태그로 승격할 것.
    """
    if df.empty:
        return df
    if universe is None or universe.empty:
        df["tickers"] = [[] for _ in range(len(df))]
        return df

    STOPWORDS = {"A", "ALL", "CAT", "KEY", "IT", "ON", "SO", "NOW", "GO", "PM",
                 "CEO", "US", "AI", "EPS", "NEW", "FOR", "ARE", "HAS", "BE", "DD"}
    name_map: dict[str, str] = {}
    tick_pat: dict[str, re.Pattern] = {}
    for _, r in universe.iterrows():
        t = str(r["ticker"]).upper()
        if len(t) >= 3 and t not in STOPWORDS:
            tick_pat[t] = re.compile(rf"\b{re.escape(t)}\b")
        nm = str(r.get("name", "")).strip()
        if len(nm) >= 5:
            core = re.sub(r"\b(Inc|Corp|Corporation|Company|Co|Ltd|plc|Group|Holdings)\b\.?", "", nm)
            core = core.replace(",", "").strip()
            if len(core) >= 4:
                name_map[core.lower()] = t

    tags: list[list[str]] = []
    for _, r in df.iterrows():
        text = f"{r.get('headline','')} {r.get('summary','')}"
        low = text.lower()
        found: set[str] = set()
        for core, t in name_map.items():
            if core in low:
                found.add(t)
        upper = text.upper()
        for t, pat in tick_pat.items():
            if pat.search(upper):
                found.add(t)
        tags.append(sorted(found))
    df = df.copy()
    df["tickers"] = tags
    return df


def filter_window(df: pd.DataFrame, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
    """[start, end) 로 자른다. look-ahead 차단의 핵심 지점."""
    if df.empty:
        return df
    ts = pd.to_datetime(df["published_at"], utc=True)
    return df[(ts >= start_utc) & (ts < end_utc)].copy()
