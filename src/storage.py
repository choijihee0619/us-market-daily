"""append-only parquet 패널 저장소.

같은 키가 다시 들어오면 나중 값으로 덮어쓴다(upsert). 데이터 소스가 사후 수정되는
경우(FRED 잠정치→확정치, Ken French 팩터 소급 갱신)를 흡수하기 위함이다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from .config import DATA_DIR

SCHEMAS: dict[str, list[str]] = {
    "prices": ["date", "ticker"],
    "macro": ["date", "series"],
    "factors": ["date"],
    "news": ["id"],
    "events": ["date", "type", "key"],
    "residuals": ["date", "ticker"],
    "signals": ["date", "ticker"],
    "scorecard": ["date"],
}


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.parquet"


def as_list(v) -> list:
    """리스트형 셀을 항상 파이썬 list로 정규화한다.

    왜 필요한가: 리스트를 담은 열(news.tickers, news.topic, news.av_topics)을
    parquet에 쓰고 되읽으면 **list가 numpy.ndarray로 돌아온다.** 그래서
    `x or []` 나 `if x:` 같은 진리값 검사가 배열에서 ValueError로 터지고
    (`The truth value of an empty array is ambiguous`), `isinstance(x, list)`
    검사는 조용히 False가 되어 값이 있는데도 없는 것으로 처리된다.

    실제로 두 가지가 동시에 일어났다(2026-07-30).
      - build_topic_matrix: --dry-run(=저장소에서 되읽는 경로)에서만 예외
      - seed_topics: AV 토픽이 있는데도 isinstance 검사에 걸려 전량 폐기
        -> 'AV 사전분류 0건' + LLM 분류 호출 낭비
    앞의 것은 죽어서 알았고 뒤의 것은 조용해서 못 알아챘다. 후자가 더 위험하다.
    """
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (tuple, set)):
        return list(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, pd.Series):
        return v.tolist()
    if isinstance(v, float) and pd.isna(v):      # 결측은 NaN 스칼라로 온다
        return []
    if isinstance(v, str):                       # 문자열은 문자 단위로 쪼개지 않는다
        return [v] if v else []
    try:
        return list(v)
    except TypeError:
        return []


def read(name: str) -> pd.DataFrame:
    p = _path(name)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def upsert(name: str, df: pd.DataFrame, keys: Sequence[str] | None = None) -> int:
    """신규 행을 병합하고 저장. 반환값은 저장 후 전체 행 수."""
    if df is None or df.empty:
        return len(read(name))
    keys = list(keys or SCHEMAS.get(name, []))
    if not keys:
        raise ValueError(f"'{name}'의 키가 정의되지 않음")

    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()

    old = read(name)
    if old.empty:
        merged = df
    else:
        merged = pd.concat([old, df], ignore_index=True)
    merged = merged.drop_duplicates(subset=keys, keep="last")
    sort_cols = [c for c in ("date", "ticker", "series", "published_at") if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols).reset_index(drop=True)

    _path(name).parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(_path(name), index=False)
    return len(merged)


def last_date(name: str) -> pd.Timestamp | None:
    df = read(name)
    if df.empty or "date" not in df.columns:
        return None
    return pd.to_datetime(df["date"]).max()


def summary() -> pd.DataFrame:
    rows = []
    for name in SCHEMAS:
        df = read(name)
        rows.append(
            {
                "table": name,
                "rows": len(df),
                "first": pd.to_datetime(df["date"]).min() if "date" in df.columns and not df.empty else None,
                "last": pd.to_datetime(df["date"]).max() if "date" in df.columns and not df.empty else None,
            }
        )
    return pd.DataFrame(rows)
