"""FRED 매크로 시계열 수집.

FRED는 잠정치를 사후 수정한다. storage.upsert가 같은 (date, series) 키를 덮어쓰므로
확정치가 들어오면 자동 교체된다. 다만 '리포트 발행 시점에 무엇을 보고 있었는가'가
중요한 연구에서는 ALFRED vintage를 써야 한다 -- 2단계 과제로 남겨둠.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd
import requests

log = logging.getLogger(__name__)
BASE = "https://api.stlouisfed.org/fred/series/observations"


def fetch_fred(series_ids: Iterable[str], api_key: str, start: str, end: str) -> pd.DataFrame:
    """long-format: date, series, value"""
    if not api_key:
        log.warning("FRED_API_KEY 없음 -- 매크로 수집 건너뜀")
        return pd.DataFrame()

    rows = []
    for sid in series_ids:
        params = {
            "series_id": sid,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": str(start),
            "observation_end": str(end),
        }
        try:
            r = requests.get(BASE, params=params, timeout=20)
            r.raise_for_status()
            obs = r.json().get("observations", [])
        except Exception as e:
            log.warning("FRED %s 실패: %s", sid, e)
            continue
        for o in obs:
            if o.get("value") in (".", "", None):
                continue  # 휴일·미발표는 '.' 으로 온다
            rows.append({"date": o["date"], "series": sid, "value": float(o["value"])})
        time.sleep(0.12)  # 레이트리밋 여유

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df


def to_wide(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return df.pivot_table(index="date", columns="series", values="value").sort_index()


BP_SERIES = {"DGS2", "DGS10", "T10Y2Y", "T10YIE", "DFF", "BAMLH0A0HYM2"}


def daily_changes(wide: pd.DataFrame, session: pd.Timestamp) -> dict[str, dict]:
    """시리즈별 최신 관측치와 그 직전 관측치 대비 변화.

    **ffill로 채운 뒤 마지막 두 행을 비교하면 안 된다.** FRED 일간 시리즈는
    공개 지연이 시리즈마다 다르다(2026-07-30 실측: 세션 07-29 시점에 T10Y2Y·T10YIE는
    07-29까지 있고, DGS2·DGS10·VIXCLS·HY OAS·DFF는 07-28까지, 광의 달러지수는
    07-24까지). ffill을 하면 07-28 값이 07-29 행으로 복사되고 직전 행과 같아져
    **변화량이 실제와 무관하게 0으로 나온다.** 그러면 1번 블록(팩트)이 "10년물
    +0bp"라는 없는 사실을 싣게 된다.

    그래서 시리즈마다 자기 실제 관측치 두 개를 비교하고, 그 값이 어느 날짜
    기준인지(asof)와 세션일 값이 아직 미공개인지(stale)를 함께 돌려준다.
    보고 단계에서 기준일을 표시할 수 있어야 한다.
    """
    if wide.empty:
        return {}
    session = pd.Timestamp(session)

    out: dict[str, dict] = {}
    for col in wide.columns:
        s = wide[col].loc[wide.index <= session].dropna()
        if s.empty:
            continue
        asof = pd.Timestamp(s.index[-1])
        level = float(s.iloc[-1])
        change = float(s.iloc[-1] - s.iloc[-2]) if len(s) >= 2 else None
        out[col] = {
            "level": level,
            "change": (change * 100 if col in BP_SERIES else change)
                      if change is not None else None,
            "unit": "bp" if col in BP_SERIES else "pt",
            "asof": asof,
            "stale": asof != session,          # 세션일 값이 아직 공개되지 않았다
            "prev_asof": pd.Timestamp(s.index[-2]) if len(s) >= 2 else None,
        }
    return out
