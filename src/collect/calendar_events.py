"""다음 거래일 주요 지표 일정 (FRED 릴리스 캘린더).

리포트 5번 블록은 원래 구현이 없었다. `ctx["upcoming"]`을 채우는 코드가 아예 없어서
영구히 "등록된 일정 없음"만 출력됐다(2026-07-30 발견).

**이 섹션의 목적은 전망이 아니다.** 전망을 하면 이 프로젝트의 포지셔닝이 무너진다.
목적은 "다음 거래일이 매크로 이벤트 데이로 잡히는가"를 미리 표시하는 것이다. 그런
날은 전 종목의 잔차가 같은 방향으로 움직여 횡단면 상관이 커지고, 3번 블록의 개별
종목 해석이 오염된다. 2단계 event study에서 event-date clustering이 치명적이라는
문제(CLAUDE.md 7장 12번)와 같은 이야기이고, 미리 표시해 두면 나중에 그 날짜를
분석에서 따로 취급할 수 있다.

FRED `releases/dates`는 기존 FRED_API_KEY로 무료다. 다만 8일치가 286건이나 되고
대부분 노이즈다(Bankrate Monitor, Coinbase Cryptocurrencies, Dow Jones Averages).
그래서 **화이트리스트 방식**으로 간다. 새 지표를 놓칠 위험이 있지만, 노이즈 250건을
싣는 것보다 낫다.
"""
from __future__ import annotations

import logging

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE = "https://api.stlouisfed.org/fred/releases/dates"

# FRED 릴리스명 부분일치 -> (표시명, 왜 중요한가)
# '왜 중요한가'를 함께 두는 이유: 지표 이름만 나열하면 배경지식이 없는 독자에게
# "Employment Situation"이 무엇인지 알 수 없다. 한 줄 설명이 붙어야 정보가 된다.
WATCHLIST: dict[str, tuple[str, str]] = {
    "Employment Situation": (
        "고용보고서 (비농업 취업자·실업률)",
        "연준 정책 판단의 1순위 입력. 서프라이즈가 금리 곡선 전체를 움직인다"),
    "Consumer Price Index": (
        "소비자물가지수 (CPI)",
        "인플레이션 지표 중 시장 반응이 가장 크다. 실질금리 기대를 바꾼다"),
    "Producer Price Index": (
        "생산자물가지수 (PPI)",
        "기업 투입비용. 마진 압박 경로로 실적 기대에 반영된다"),
    "Personal Income and Outlays": (
        "개인소득·지출 (PCE 물가 포함)",
        "연준이 공식 목표로 쓰는 물가지표가 여기 들어 있다"),
    "Advance Monthly Sales for Retail": (
        "소매판매",
        "미국 GDP의 약 3분의 2가 소비다. 경기소비재 섹터에 직접 반영된다"),
    "Gross Domestic Product": (
        "GDP",
        "성장률 확정치·수정치. 경기 국면 판단의 기준선"),
    "FOMC Press Release": (
        "FOMC 성명",
        "정책금리 결정. 전 종목 잔차가 같은 방향으로 움직이는 대표적인 날"),
    "FOMC Minutes": (
        "FOMC 의사록",
        "3주 전 회의의 내부 논의 공개. 향후 경로에 대한 단서"),
    "Unemployment Insurance Weekly Claims": (
        "주간 신규 실업수당 청구",
        "주 단위로 나오는 유일한 고용 지표. 고빈도 경기 체크"),
    "ISM Manufacturing": (
        "ISM 제조업 PMI",
        "50 기준선 위/아래로 확장·수축을 가른다. 산업재·소재에 민감"),
    "ISM Services": (
        "ISM 서비스업 PMI",
        "미국 경제의 대부분이 서비스업이다"),
    "University of Michigan": (
        "미시간대 소비자심리지수",
        "기대인플레이션 항목이 함께 나와 채권시장이 본다"),
    "Job Openings and Labor Turnover": (
        "JOLTS 구인건수",
        "노동 수요의 강도. 임금 상승 압력 판단에 쓰인다"),
    "Industrial Production": (
        "산업생산·설비가동률",
        "제조업 실물 활동. 경기 순환 국면 확인"),
    "New Residential Construction": (
        "주택 착공·허가",
        "금리에 가장 민감한 실물 부문. 건자재 종목에 직접 반영된다"),
}


def fetch_upcoming(api_key: str | None, session: pd.Timestamp,
                   horizon_days: int = 5, limit_out: int = 8) -> list[dict]:
    """session 다음 거래일부터 horizon_days 안의 주요 지표 일정.

    반환: [{date, name, why, release}] — 날짜 오름차순.
    실패하거나 키가 없으면 빈 리스트(리포트는 '예정된 지표 없음'으로 처리).
    """
    if not api_key:
        log.info("FRED_API_KEY 없음 -- 일정 수집 건너뜀")
        return []

    start = (pd.Timestamp(session) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(session) + pd.Timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    try:
        r = requests.get(BASE, params={
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": start,
            "realtime_end": end,
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "limit": 1000,
        }, timeout=20)
        r.raise_for_status()
        rows = r.json().get("release_dates", [])
    except Exception as e:
        log.warning("FRED 릴리스 캘린더 실패: %s", e)
        return []

    return filter_watchlist(rows, limit_out)


def filter_watchlist(rows: list[dict], limit_out: int = 8) -> list[dict]:
    """릴리스 목록에서 화이트리스트에 걸리는 것만 남긴다.

    같은 지표가 기간 안에 여러 번 나오면 가장 이른 날짜만 쓴다(주간 지표 중복 방지).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("release_name") or "")
        date = str(row.get("date") or "")
        if not name or not date:
            continue
        for key, (label, why) in WATCHLIST.items():
            if key.lower() in name.lower():
                if label in seen:
                    break
                seen.add(label)
                out.append({"date": date, "name": label, "why": why, "release": name})
                break
    out.sort(key=lambda e: (e["date"], e["name"]))
    return out[:limit_out]
