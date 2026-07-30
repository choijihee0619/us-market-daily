"""GA4 + AdSense 성과 수집 (읽기 전용).

브라우저 자동화가 필요 없다. 둘 다 공식 API가 있다.
  - GA4 Data API      : 서비스 계정 지원. 가장 깔끔하다.
  - AdSense Management API v2 : accounts.reports.generate 로 실적 조회.

**AdSense는 서비스 계정을 지원하지 않는 것으로 알려져 있다.** AdSense 계정이
개인 Google 계정에 묶여 있어 사용자 OAuth 동의 흐름이 필요하다. 그래서 최초 1회
브라우저 인증 후 refresh token을 저장해 재사용한다.  [검증 필요 -- 최초 구성 시
공식 문서 확인할 것]

키가 없으면 CSV 폴백으로 간다. GA4와 AdSense 웹 UI에서 CSV를 내려받아
data/analytics/ 에 넣으면 동일한 스키마로 읽는다. 그러면 API 구성 전에도
교차분석을 돌려볼 수 있다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from ..config import DATA_DIR, env

log = logging.getLogger(__name__)

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ADSENSE_SCOPE = "https://www.googleapis.com/auth/adsense.readonly"

TRAFFIC_COLS = ["date", "path", "views", "users", "avg_duration_s"]
EARNINGS_COLS = ["date", "path", "earnings", "impressions", "clicks", "rpm"]


# ----------------------------------------------------------------- GA4

def fetch_ga4(property_id: str | None = None, days: int = 30) -> pd.DataFrame:
    property_id = property_id or env("GA4_PROPERTY_ID")
    creds_path = env("GOOGLE_APPLICATION_CREDENTIALS")
    if not property_id or not creds_path:
        log.info("GA4 미구성 (GA4_PROPERTY_ID / GOOGLE_APPLICATION_CREDENTIALS) -- 건너뜀")
        return pd.DataFrame(columns=TRAFFIC_COLS)

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange, Dimension, Metric, RunReportRequest,
        )
    except ImportError:
        log.warning("google-analytics-data 미설치: pip install google-analytics-data")
        return pd.DataFrame(columns=TRAFFIC_COLS)

    try:
        client = BetaAnalyticsDataClient()
        req = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
            dimensions=[Dimension(name="date"), Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews"), Metric(name="activeUsers"),
                     Metric(name="averageSessionDuration")],
            limit=100000,
        )
        resp = client.run_report(req)
    except Exception as e:
        log.warning("GA4 조회 실패: %s", e)
        return pd.DataFrame(columns=TRAFFIC_COLS)

    rows = []
    for r in resp.rows:
        d, path = r.dimension_values[0].value, r.dimension_values[1].value
        rows.append({
            "date": pd.to_datetime(d, format="%Y%m%d"),
            "path": path,
            "views": int(float(r.metric_values[0].value or 0)),
            "users": int(float(r.metric_values[1].value or 0)),
            "avg_duration_s": float(r.metric_values[2].value or 0),
        })
    log.info("GA4 %d행", len(rows))
    return pd.DataFrame(rows, columns=TRAFFIC_COLS)


# ------------------------------------------------------------- AdSense

def fetch_adsense(account: str | None = None, days: int = 30) -> pd.DataFrame:
    """AdSense Management API v2. OAuth refresh token 필요."""
    account = account or env("ADSENSE_ACCOUNT")     # 예: accounts/pub-1234567890
    client_id = env("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = env("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = env("GOOGLE_OAUTH_REFRESH_TOKEN")

    if not all([account, client_id, client_secret, refresh_token]):
        log.info("AdSense 미구성 -- 건너뜀 (ADSENSE_ACCOUNT / GOOGLE_OAUTH_*)")
        return pd.DataFrame(columns=EARNINGS_COLS)

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("google-api-python-client 미설치")
        return pd.DataFrame(columns=EARNINGS_COLS)

    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=[ADSENSE_SCOPE],
        )
        svc = build("adsense", "v2", credentials=creds, cache_discovery=False)
        end = pd.Timestamp.today().normalize()
        start = end - pd.Timedelta(days=days)
        resp = (
            svc.accounts()
            .reports()
            .generate(
                account=account,
                dateRange="CUSTOM",
                startDate_year=start.year, startDate_month=start.month, startDate_day=start.day,
                endDate_year=end.year, endDate_month=end.month, endDate_day=end.day,
                dimensions=["DATE", "PAGE_URL"],
                metrics=["ESTIMATED_EARNINGS", "IMPRESSIONS", "CLICKS", "IMPRESSIONS_RPM"],
                orderBy=["-ESTIMATED_EARNINGS"],
                limit=50000,
            )
            .execute()
        )
    except Exception as e:
        log.warning("AdSense 조회 실패: %s", e)
        return pd.DataFrame(columns=EARNINGS_COLS)

    rows = []
    for r in resp.get("rows", []):
        c = [x.get("value", "") for x in r.get("cells", [])]
        if len(c) < 6:
            continue
        rows.append({
            "date": pd.to_datetime(c[0], errors="coerce"),
            "path": _url_to_path(c[1]),
            "earnings": float(c[2] or 0),
            "impressions": int(float(c[3] or 0)),
            "clicks": int(float(c[4] or 0)),
            "rpm": float(c[5] or 0),
        })
    log.info("AdSense %d행", len(rows))
    return pd.DataFrame(rows, columns=EARNINGS_COLS)


def _url_to_path(url: str) -> str:
    m = re.match(r"https?://[^/]+(/.*)$", str(url))
    return m.group(1) if m else str(url)


# ---------------------------------------------------------- CSV 폴백

def load_csv_fallback(kind: str) -> pd.DataFrame:
    """웹 UI에서 내려받은 CSV를 읽는다. data/analytics/{kind}.csv

    컬럼명이 언어·버전마다 달라서 유연하게 매핑한다.
    """
    p = DATA_DIR / "analytics" / f"{kind}.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(p)
    except Exception as e:
        log.warning("CSV 폴백 읽기 실패 %s: %s", p, e)
        return pd.DataFrame()

    low = {c.lower().strip(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n in low:
                return low[n]
        return None

    out = pd.DataFrame()
    c = pick("date", "날짜", "day")
    if c:
        out["date"] = pd.to_datetime(df[c], errors="coerce")
    c = pick("page path", "pagepath", "path", "page", "url", "페이지 경로")
    if c:
        out["path"] = df[c].map(_url_to_path)

    if kind == "traffic":
        for tgt, names in [("views", ("views", "screenpageviews", "pageviews", "조회수")),
                           ("users", ("users", "activeusers", "사용자")),
                           ("avg_duration_s", ("averagesessionduration", "avg_duration_s"))]:
            c = pick(*names)
            out[tgt] = pd.to_numeric(df[c], errors="coerce") if c else 0
    else:
        for tgt, names in [("earnings", ("estimated earnings", "earnings", "예상 수입")),
                           ("impressions", ("impressions", "노출수")),
                           ("clicks", ("clicks", "클릭수")),
                           ("rpm", ("impressions rpm", "rpm", "page rpm"))]:
            c = pick(*names)
            out[tgt] = pd.to_numeric(df[c], errors="coerce") if c else 0

    log.info("CSV 폴백 %s: %d행", kind, len(out))
    return out.dropna(subset=["date"]) if "date" in out.columns else out


def collect_all(days: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    traffic = fetch_ga4(days=days)
    if traffic.empty:
        traffic = load_csv_fallback("traffic")
    earnings = fetch_adsense(days=days)
    if earnings.empty:
        earnings = load_csv_fallback("earnings")
    return traffic, earnings
