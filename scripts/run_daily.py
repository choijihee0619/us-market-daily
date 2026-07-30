#!/usr/bin/env python3
"""일간 파이프라인 오케스트레이터.

사용:
    python scripts/run_daily.py                    # 마감 완료된 최근 거래일
    python scripts/run_daily.py --session 2026-07-24
    python scripts/run_daily.py --backfill 30      # 과거 30일 가격/팩터만 적재
    python scripts/run_daily.py --dry-run          # 수집 없이 저장된 데이터로 리포트만
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.storage import as_list  # noqa: E402
from src.calendar_utils import (  # noqa: E402
    is_dst_in_us, kst_publish_stamp, last_completed_session, news_window, previous_session,
)
from src.collect import factors as F  # noqa: E402
from src.collect import macro as M  # noqa: E402
from src.collect import news as N  # noqa: E402
from src.collect import news_alphavantage as AV  # noqa: E402
from src.collect import prices as P  # noqa: E402
from src.config import OUT_DIR, env, load_config  # noqa: E402
from src.llm.base import get_provider  # noqa: E402
from src.process import attribution as A  # noqa: E402
from src.process import residual as R  # noqa: E402
from src.process import sentiment as S  # noqa: E402
from src.report import builder as B  # noqa: E402
from src.report import charts as C  # noqa: E402
from src.publish import github_archive as GH  # noqa: E402
from src.publish import naver_package as NAVER  # noqa: E402
from src.publish import tistory_package as TIST  # noqa: E402
from src.publish import url_registry as REG  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("run_daily")


def git_rev() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def resolve_universe(cfg) -> pd.DataFrame:
    path = Path(cfg.get_path("universe.constituents_file", "data/universe_sp500.csv"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    if path.exists():
        return pd.read_csv(path)
    log.info("구성종목 파일 없음 -> 위키피디아에서 수집")
    u = P.sp500_constituents()
    if not u.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        u.to_csv(path, index=False)
    return u


def collect(cfg, session: pd.Timestamp, lookback_days: int) -> None:
    universe = resolve_universe(cfg)
    names = list(universe["ticker"]) if not universe.empty else []
    names = names[: int(cfg.get_path("universe.max_names", 500))]

    etfs = (list(cfg.get_path("universe.benchmarks", []))
            + list(cfg.get_path("universe.sectors", {}).keys())
            + list(cfg.get_path("universe.style_proxies", {}).keys())
            + list(cfg.get_path("signals.yahoo_signals", {}).keys()))

    start = (session - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = session.strftime("%Y-%m-%d")

    log.info("가격 수집: %d 종목, %s ~ %s", len(set(names + etfs)), start, end)
    px = P.fetch_prices(names + etfs, start, end)
    if not px.empty:
        storage.upsert("prices", px, ["date", "ticker"])
        log.info("prices 저장 %d행", len(px))

    fred_key = env("FRED_API_KEY")
    mac = M.fetch_fred(list(cfg.get_path("signals.fred_series", {}).keys()), fred_key, start, end)
    if not mac.empty:
        storage.upsert("macro", mac, ["date", "series"])
        log.info("macro 저장 %d행", len(mac))

    french = F.fetch_french_daily()
    proxy = F.proxy_factors(storage.read("prices"))
    fac = F.merge_factors(french, proxy)
    if not fac.empty:
        fac = fac[pd.to_datetime(fac["date"]) >= pd.Timestamp(start)]
        storage.upsert("factors", fac, ["date"])
        log.info("factors 저장 %d행 (french=%d)", len(fac), len(french))

    providers = list(cfg.get_path("news.providers", ["rss"]))
    win_start, _ = news_window(session)

    # Alpha Vantage: ticker_sentiment에 relevance_score가 있어 태깅이 정확하다.
    av = pd.DataFrame()
    if "alphavantage" in providers and cfg.get_path("news.alphavantage.enabled", False):
        av = AV.fetch_news(
            env("ALPHAVANTAGE_API_KEY"),
            time_from=win_start,
            topic_batches=cfg.get_path("news.alphavantage.topic_batches"),
            limit=int(cfg.get_path("news.alphavantage.limit", 1000)),
            relevance_min=float(cfg.get_path("news.alphavantage.relevance_min", 0.25)),
            max_calls=int(cfg.get_path("news.alphavantage.max_calls_per_day", 4)),
        )

    rss = pd.DataFrame()
    if "rss" in providers:
        rss = N.fetch_rss(
            cfg.get_path("news.rss_feeds", []),
            user_agent=cfg.get_path("news.rss_user_agent") or env("SEC_USER_AGENT"),
        )
        if cfg.get_path("news.edgar.enabled", False):
            ed = N.fetch_edgar(
                cfg.get_path("news.edgar.forms", ["8-K"]),
                env("SEC_USER_AGENT")
                or cfg.get_path("news.edgar.user_agent", "research contact@example.com"),
            )
            if not ed.empty:
                rss = pd.concat([rss, ed], ignore_index=True)
        if not rss.empty:
            rss = N.tag_tickers(rss, universe)   # AV와 달리 RSS는 직접 태깅해야 한다

    nw = AV.merge_with_rss(av, rss)
    if not nw.empty:
        hist = storage.read("news")
        nw = S.score_dataframe(nw, hist, cfg.get_path("sentiment.novelty_threshold", 0.85))
        nw = AV.seed_topics(nw)                  # AV 토픽을 초기값으로 -> LLM 호출 절감
        storage.upsert("news", nw, ["id"])
        log.info("news 저장 %d행 (AV %d / RSS %d)", len(nw), len(av), len(rss))


def classify(cfg, session: pd.Timestamp) -> pd.DataFrame:
    """세션 창의 뉴스에 토픽 라벨을 붙인다."""
    news = storage.read("news")
    if news.empty:
        return news
    start, end = news_window(session)
    win = N.filter_window(news, start, end)
    if win.empty:
        log.warning("세션 창 %s ~ %s 에 뉴스 0건", start, end)
        return win

    topics = list(cfg.get_path("llm.topics", []))
    maxn = int(cfg.get_path("llm.max_articles_to_classify", 120))

    # Alpha Vantage가 이미 토픽을 붙인 기사는 LLM에 다시 보내지 않는다.
    # storage.as_list를 거치는 이유: news를 parquet에서 되읽으면 topic이 numpy
    # 배열로 돌아와 isinstance(v, list) 검사가 전부 False가 된다. 그러면 AV가
    # 붙여준 토픽 1000여 건이 '미분류'로 잡혀 LLM 호출을 낭비하고, 아래 tail에서
    # 빈 리스트로 덮어써 실제로 삭제된다(2026-07-30 실측: 사전분류 0건으로 표시).
    def _has_topic(v) -> bool:
        return len(as_list(v)) > 0

    if "topic" not in win.columns:
        win = win.copy()
        win["topic"] = [[] for _ in range(len(win))]
    pre = win[win["topic"].apply(_has_topic)]
    todo = win[~win["topic"].apply(_has_topic)]

    # novelty 높은 순으로 잘라 분류 비용을 통제한다 (재탕 기사는 분류 가치가 낮다)
    todo = todo.sort_values("novelty", ascending=False)
    head, tail = todo.head(maxn), todo.tail(max(0, len(todo) - maxn))

    provider = get_provider(cfg)
    log.info("토픽 분류: provider=%s · LLM %d건 · AV 사전분류 %d건 · 미분류 %d건",
             provider.name, len(head), len(pre), len(tail))

    if not head.empty:
        head = head.copy()
        head["topic"] = provider.classify_topics(head["headline"].fillna("").tolist(), topics)
    if not tail.empty:
        # 분류 예산을 넘긴 기사는 LLM에 보내지 않을 뿐, 기존 토픽을 지우지 않는다.
        # 빈 리스트로 덮어쓰면 AV가 붙여준 토픽까지 날아간다.
        tail = tail.copy()
        tail["topic"] = tail["topic"].apply(as_list)

    out = pd.concat([pre, head, tail], ignore_index=True)
    storage.upsert("news", out, ["id"])
    return out


def build_context(cfg, session: pd.Timestamp, news_win: pd.DataFrame) -> dict:
    prices = storage.read("prices")
    fac = storage.read("factors")
    mac_long = storage.read("macro")
    sectors_map = dict(cfg.get_path("universe.sectors", {}))

    ctx: dict = {
        "session": session,
        "published_kst": kst_publish_stamp(session),
        "spec": cfg.get_path("model.risk_model", "ff5_umd"),
        "beta_window": cfg.get_path("model.beta_window", 250),
        "git_rev": git_rev(),
        "sources": ["Yahoo Finance", "FRED (St. Louis Fed)",
                    "Kenneth R. French Data Library", "SEC EDGAR"],
        "dst": is_dst_in_us(session),
    }

    day = prices[pd.to_datetime(prices["date"]).dt.normalize() == session] if not prices.empty else pd.DataFrame()
    ctx["benchmarks"] = {
        t: float(day.loc[day["ticker"] == t, "ret"].iloc[0])
        for t in cfg.get_path("universe.benchmarks", [])
        if not day.empty and (day["ticker"] == t).any()
        and pd.notna(day.loc[day["ticker"] == t, "ret"].iloc[0])
    }

    sect_df = A.sector_breakdown(prices, session, sectors_map)
    ctx["sectors_df"] = sect_df
    ctx["sectors"] = sect_df.to_dict("records")

    ctx["factors"] = A.factor_breakdown(fac, session)

    mac_wide = M.to_wide(mac_long)
    ctx["macro_wide"] = mac_wide
    ctx["macro"] = M.daily_changes(mac_wide, session)

    resid = R.estimate_residuals(
        prices, fac, session,
        spec=cfg.get_path("model.risk_model", "ff5_umd"),
        window=int(cfg.get_path("model.beta_window", 250)),
        min_obs=int(cfg.get_path("model.beta_min_obs", 120)),
        shrinkage=cfg.get_path("model.beta_shrinkage", "vasicek"),
    )
    if not resid.empty:
        storage.upsert("residuals", resid, ["date", "ticker"])
    ctx["resid_df"] = resid
    ctx["cross_section"] = R.cross_section_stats(
        resid, float(cfg.get_path("model.outlier_sigma", 2.0))
    )

    topics = list(cfg.get_path("llm.topics", []))
    tmat = A.build_topic_matrix(news_win, topics) if not news_win.empty else pd.DataFrame()
    ctx["topic_regression"] = A.topic_regression(resid, tmat) if not tmat.empty else {"r2": None, "coef": {}, "n": 0}

    # 이례치 종목별 매칭 헤드라인
    news_map: dict[str, str] = {}
    if not news_win.empty and "tickers" in news_win.columns:
        ex = news_win.explode("tickers").rename(columns={"tickers": "ticker"})
        cand = {r["ticker"] for r in (ctx["cross_section"].get("top", [])
                                      + ctx["cross_section"].get("bottom", []))}
        for t in cand:
            sub = ex[ex["ticker"] == t]
            if not sub.empty:
                news_map[t] = str(sub.sort_values("novelty", ascending=False).iloc[0]["headline"])
    ctx["outlier_news"] = news_map

    # 신호 저장 + 어제 신호 채점
    sig = S.aggregate_by_ticker(news_win) if not news_win.empty else pd.DataFrame()
    if not sig.empty:
        sig["date"] = session
        storage.upsert("signals", sig, ["date", "ticker"])

    all_sig = storage.read("signals")
    prev_sig = pd.DataFrame()
    if not all_sig.empty:
        try:
            prev = previous_session(session)
            prev_sig = all_sig[pd.to_datetime(all_sig["date"]).dt.normalize() == prev]
        except ValueError:
            pass
    sc = A.scorecard(prev_sig, resid)
    ctx["scorecard"] = sc
    if sc.get("available"):
        storage.upsert("scorecard", pd.DataFrame([{**sc, "date": session}]), ["date"])
    ctx["scorecard_cum"] = _cum_scorecard()
    return ctx


def _cum_scorecard() -> dict:
    df = storage.read("scorecard")
    if df.empty or "spread_bp" not in df.columns:
        return {"n_days": 0}
    s = pd.to_numeric(df["spread_bp"], errors="coerce").dropna()
    if len(s) < 2:
        return {"n_days": int(len(s))}
    t = float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if s.std(ddof=1) > 0 else float("nan")
    return {
        "n_days": int(len(s)),
        "mean_bp": float(s.mean()),
        "hit_rate": float((s > 0).mean()),
        "t_stat": t,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="YYYY-MM-DD (ET 기준 거래일)")
    ap.add_argument("--lookback", type=int, default=420, help="가격 수집 소급 일수")
    ap.add_argument("--backfill", type=int, default=0, help="수집만 하고 리포트는 건너뜀")
    ap.add_argument("--dry-run", action="store_true", help="외부 수집 없이 저장 데이터로만 생성")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.session:
        session = pd.Timestamp(args.session).normalize()
    else:
        session = last_completed_session()
        if session is None:
            log.info("아직 마감 완료된 거래일이 없다. 종료.")
            return 0
    log.info("대상 거래일: %s (%s)", session.date(), "EDT" if is_dst_in_us(session) else "EST")

    if args.backfill:
        collect(cfg, session, args.backfill)
        print(storage.summary().to_string(index=False))
        return 0

    if not args.dry_run:
        collect(cfg, session, args.lookback)
        news_win = classify(cfg, session)
    else:
        news = storage.read("news")
        start, end = news_window(session)
        news_win = N.filter_window(news, start, end) if not news.empty else pd.DataFrame()

    ctx = build_context(cfg, session, news_win)

    outdir = OUT_DIR / session.strftime("%Y-%m-%d")
    charts = C.build_all(ctx, outdir / "images", int(cfg.get_path("report.charts.dpi", 144)))
    log.info("차트 %d장 생성", len(charts))

    provider = get_provider(cfg)
    narrative_ctx = {
        k: ctx[k] for k in ("factors", "sectors", "macro", "topic_regression",
                            "cross_section", "benchmarks", "scorecard") if k in ctx
    }
    narrative_ctx["session"] = str(session.date())
    narrative = provider.write_narrative(narrative_ctx)

    banned = B.check_banned(narrative)
    if banned:
        log.warning("금지 표현 발견: %s -- 원고를 확인할 것", banned)

    md = B.build_markdown(ctx, narrative, [f"images/{Path(p).name}" for p in charts], cfg)
    title = B.make_title(ctx)

    # canonical은 수익이 나는 쪽(티스토리)을 가리킨다.
    # 숫자형 주소는 발행 시점에 URL이 정해지므로 여기서는 만들 수 없다.
    # 이미 기록된 게 있으면(재실행) 그걸 쓰고, 없으면 None으로 두고
    # 발행 후 scripts/link_post.py 가 소급 채운다.
    site = str(cfg.get_path("report.site_url", "") or "").rstrip("/")
    site = site if site and "example.com" not in site else ""
    mode = str(cfg.get_path("report.post_url_mode", "numeric")).lower()

    repo_root = Path(__file__).resolve().parents[1]
    canonical = None
    if site:
        rec = REG.get(repo_root, session, "daily")
        if rec:
            canonical = rec["url"]
        elif mode == "date":
            path = str(cfg.get_path("report.post_path", "/{date}")).format(
                date=session.strftime("%Y-%m-%d")
            )
            canonical = f"{site}{path}"
        else:
            log.info("숫자형 주소 모드 -- 발행 후 다음을 실행할 것: "
                     "python scripts/link_post.py https://도메인/글번호")
    repo_url = str(cfg.get_path("report.repo_url", "") or "") or None
    if repo_url and "USER/" in repo_url:
        repo_url = None

    channels = list(cfg.get_path("report.channels", ["github", "tistory", "naver"]))
    made: list[str] = []

    if "github" in channels:
        p = GH.write_post(session, title, md, ctx, charts, repo_root, canonical)
        GH.append_scorecard_json(ctx, repo_root)
        made.append(f"github   {p.relative_to(repo_root)}")

    if "tistory" in channels:
        p = TIST.write_package(session, title, md, ctx, charts, OUT_DIR, repo_url)
        made.append(f"tistory  {p}")

    if "naver" in channels:
        p = NAVER.write_package(session, title, ctx, charts, OUT_DIR, canonical)
        made.append(f"naver    {p}")

    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    for m in made:
        print("  " + m)
    if canonical is None:
        print()
        print("  [주의] config.yaml 의 site_url / repo_url 이 아직 예시값이다.")
        print("         본인 도메인으로 교체해야 canonical 링크가 들어간다.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
