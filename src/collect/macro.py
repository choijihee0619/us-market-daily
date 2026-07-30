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


def daily_changes(wide: pd.DataFrame, session: pd.Timestamp) -> dict[str, dict]:
    """세션일의 레벨과 전일대비 변화. 금리계열은 bp, 나머지는 % 또는 pt."""
    if wide.empty:
        return {}
    w = wide.loc[wide.index <= pd.Timestamp(session)].ffill()
    if len(w) < 2:
        return {}
    cur, prev = w.iloc[-1], w.iloc[-2]

    BP = {"DGS2", "DGS10", "T10Y2Y", "T10YIE", "DFF", "BAMLH0A0HYM2"}
    out: dict[str, dict] = {}
    for col in w.columns:
        if pd.isna(cur[col]) or pd.isna(prev[col]):
            continue
        diff = float(cur[col] - prev[col])
        out[col] = {
            "level": float(cur[col]),
            "change": diff * 100 if col in BP else diff,
            "unit": "bp" if col in BP else "pt",
        }
    return out
