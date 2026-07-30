#!/usr/bin/env python3
"""주간 회고 생성기.

사용:
    python scripts/run_weekly.py                  # 직전 주(월~금)
    python scripts/run_weekly.py --week 2026-07-24
    python scripts/run_weekly.py --next "다음 주 가설 문장"
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import storage  # noqa: E402
from src.calendar_utils import last_completed_session  # noqa: E402
from src.config import OUT_DIR, load_config  # noqa: E402
from src.process import weekly_stats as W  # noqa: E402
from src.publish import github_archive as GH  # noqa: E402
from src.publish import naver_package as NAVER  # noqa: E402
from src.publish import tistory_package as TIST  # noqa: E402
from src.report import weekly_builder as WB  # noqa: E402
from src.report import weekly_charts as WC  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("run_weekly")
ROOT = Path(__file__).resolve().parents[1]


def git_rev() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def naver_summary_weekly(stats: dict, title: str, canonical: str | None) -> str:
    """주간 글의 네이버 요약본. 일간과 마찬가지로 전문을 옮기지 않는다."""
    s, e = pd.Timestamp(stats["start"]), pd.Timestamp(stats["end"])
    L = [title, "", f"검증 구간 {s:%Y-%m-%d} ~ {e:%Y-%m-%d}", ""]
    L += ["[ 이번 주 가설 ]",
          "직전 거래일 뉴스 감성이 익일 초과수익률의",
          "종목 간 차이를 설명하는가?", ""]
    if stats.get("n_week"):
        L += ["[ 이번 주 결과 ]",
              f"방향 적중  {stats['week_hits']}/{stats['n_week']}일",
              f"평균 스프레드  {stats.get('week_mean_bp', 0):+.1f}bp", ""]
    L += ["[ 누적 결과 ]",
          f"관측  {stats.get('n_total',0)}거래일",
          f"일평균 스프레드  {stats.get('cum_mean_bp',0):+.2f}bp",
          f"방향 적중률  {stats.get('cum_hit_rate',0)*100:.1f}%",
          f"t-통계량  {stats.get('cum_t',float('nan')):.2f}  (Newey-West)", ""]
    label, reason = W.verdict(stats)
    L += ["[ 판정 ]", label, "", reason, ""]
    L += ["─" * 22, "",
          "검정 절차, 팩터 모형 스펙, 한계 논의는",
          "전체 리포트에 있습니다."]
    if canonical:
        L += ["", f"전체 리포트 → {canonical}"]
    L += ["", "본 글은 공개 데이터를 자동 수집·분석한 연구 기록이며 투자자문이 아닙니다.",
          "맞은 주와 틀린 주를 모두 그대로 남깁니다."]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="해당 주의 아무 날짜 (YYYY-MM-DD). 비우면 직전 주")
    ap.add_argument("--next", dest="next_hyp", help="다음 주 가설 문장")
    ap.add_argument("--lags", type=int, default=5, help="Newey-West lag")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)

    anchor = pd.Timestamp(args.week) if args.week else (last_completed_session() or pd.Timestamp.today())
    start, end = W.week_bounds(anchor)
    log.info("검증 구간: %s ~ %s", start.date(), end.date())

    sc = W.load_scorecard(ROOT)
    if sc.empty:
        log.error("data/scorecard.json 이 비어 있다. run_daily.py 를 먼저 며칠 돌릴 것.")
        return 1

    stats = W.summarize(sc, start, end, lags=args.lags)
    if not stats.get("available"):
        log.warning("해당 주에 채점 기록이 없다 (누적 %d거래일).", stats.get("n_total", 0))
        return 1

    ctx = {
        "session": end,
        "spec": cfg.get_path("model.risk_model", "ff5_umd"),
        "beta_window": cfg.get_path("model.beta_window", 250),
        "git_rev": git_rev(),
        "weekly_factors": W.weekly_factors(storage.read("factors"), start, end),
        "recurring_outliers": W.recurring_outliers(storage.read("residuals"), start, end),
        "next_hypothesis": args.next_hyp,
    }

    slug = f"{start:%Y-%m-%d}_weekly"
    outdir = OUT_DIR / slug
    charts = WC.build_all(stats, outdir / "images", int(cfg.get_path("report.charts.dpi", 144)))
    log.info("차트 %d장", len(charts))

    md = WB.build_markdown(stats, ctx, [f"images/{p.name}" for p in charts], cfg)
    title = WB.make_title(stats)

    site = str(cfg.get_path("report.site_url", "") or "").rstrip("/")
    canonical = f"{site}/{slug}" if site and "example.com" not in site else None
    repo_url = str(cfg.get_path("report.repo_url", "") or "") or None
    if repo_url and "USER/" in repo_url:
        repo_url = None

    made: list[str] = []
    channels = list(cfg.get_path("report.channels", ["github", "tistory", "naver"]))

    if "github" in channels:
        posts = ROOT / "posts"
        posts.mkdir(exist_ok=True)
        p = GH.write_post(start, title, md, {**ctx, "session": start, "scorecard": {},
                                             "topic_regression": {}, "benchmarks": {}},
                          charts, ROOT, canonical)
        # 주간 글은 파일명을 구분한다
        target = posts / f"{slug}.md"
        p.replace(target)
        made.append(f"github   {target.relative_to(ROOT)}")

    if "tistory" in channels:
        tctx = {**ctx, "session": start, "benchmarks": {}, "macro": {},
                "topic_regression": {}, "cross_section": {},
                "scorecard": {"available": True, "spread_bp": stats.get("cum_mean_bp", 0),
                              "hit": stats.get("cum_mean_bp", 0) > 0}}
        p = TIST.write_package(start, title, md, tctx, charts, OUT_DIR, repo_url)
        made.append(f"tistory  {p}")

    if "naver" in channels:
        pkg = OUT_DIR / slug / "naver"
        (pkg / "images").mkdir(parents=True, exist_ok=True)
        (pkg / "title.txt").write_text(title, encoding="utf-8")
        (pkg / "post.txt").write_text(naver_summary_weekly(stats, title, canonical),
                                      encoding="utf-8")
        (pkg / "tags.txt").write_text(
            "미국주식, 퀀트, 백테스트, 팩터투자, 데이터분석, 시장분석", encoding="utf-8")
        import shutil

        for c in charts[:2]:
            shutil.copy(c, pkg / "images" / c.name)
        made.append(f"naver    {pkg}")

    label, _ = W.verdict(stats)
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    print(f"  판정: {label}")
    print(f"  누적 {stats['n_total']}거래일 · 일평균 {stats.get('cum_mean_bp',0):+.2f}bp · "
          f"t={stats.get('cum_t',float('nan')):.2f} · "
          f"적중률 {stats.get('cum_hit_rate',0)*100:.1f}%")
    print()
    for m in made:
        print("  " + m)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
