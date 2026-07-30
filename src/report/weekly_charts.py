"""주간 회고용 차트 3종.

일간 차트와 스타일을 공유하되, 보여주는 것이 다르다.
일간은 "오늘 무슨 일이 있었나", 주간은 "내 신호가 작동하는가".
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .charts import ACCENT, DOWN, GRID, INK, NEUTRAL, UP, _t, setup_style  # noqa: E402


def chart_cum_spread(stats: dict, outdir: Path) -> Path | None:
    """누적 스프레드 곡선. 이 블로그의 성적표 그 자체."""
    curve, dates = stats.get("cum_curve"), stats.get("cum_dates")
    if not curve or not dates or len(curve) < 3:
        return None

    d = pd.to_datetime(pd.Series(dates))
    y = np.asarray(curve, dtype=float)

    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    ax.plot(d, y, lw=2.0, color=ACCENT)
    ax.fill_between(d, 0, y, where=y >= 0, color=UP, alpha=0.10)
    ax.fill_between(d, 0, y, where=y < 0, color=DOWN, alpha=0.10)
    ax.axhline(0, color=INK, lw=1.0)

    # 이번 주 구간 음영
    s, e = stats.get("start"), stats.get("end")
    if s is not None and e is not None:
        ax.axvspan(pd.Timestamp(s), pd.Timestamp(e), color=NEUTRAL, alpha=0.13)

    ax.set_ylabel("bp")
    ax.set_title(
        _t(f"누적 신호 스프레드 · {len(y)}거래일", f"Cumulative Signal Spread · {len(y)} sessions"),
        loc="left",
    )
    t = stats.get("cum_t")
    if t is not None and not (isinstance(t, float) and np.isnan(t)):
        ax.text(
            0.985, 0.05,
            _t(f"누적 {y[-1]:+.0f}bp · 일평균 {stats['cum_mean_bp']:+.1f}bp · "
               f"Newey-West t={t:.2f}",
               f"cum {y[-1]:+.0f}bp · mean {stats['cum_mean_bp']:+.1f}bp · NW t={t:.2f}"),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=9.5, color="#555",
        )
    fig.autofmt_xdate()
    fig.tight_layout()
    p = outdir / "w01_cum_spread.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_week_daily(stats: dict, outdir: Path) -> Path | None:
    """이번 주 일별 스프레드 막대."""
    rows = stats.get("week_daily") or []
    if not rows:
        return None
    d = [pd.Timestamp(r["date"]) for r in rows]
    v = [r["bp"] for r in rows]
    labels = [x.strftime("%m/%d\n%a") for x in d]

    fig, ax = plt.subplots(figsize=(7.4, 3.9))
    ax.bar(labels, v, color=[UP if x > 0 else DOWN for x in v], width=0.55)
    ax.axhline(0, color=INK, lw=1.0)
    ax.set_ylabel("bp")
    ax.set_title(_t("이번 주 일별 신호 스프레드", "Daily Signal Spread, This Week"), loc="left")
    ax.grid(axis="x", visible=False)
    for i, x in enumerate(v):
        ax.text(i, x + (1.2 if x >= 0 else -1.2), f"{x:+.0f}",
                ha="center", va="bottom" if x >= 0 else "top", fontsize=9)
    ax.margins(y=0.24)
    fig.tight_layout()
    p = outdir / "w02_week_daily.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_hit_rate(stats: dict, outdir: Path, window: int = 20) -> Path | None:
    """롤링 적중률. 신호가 특정 국면에서만 작동하는지 본다."""
    curve, dates = stats.get("cum_curve"), stats.get("cum_dates")
    if not curve or len(curve) < window + 5:
        return None

    daily = np.diff(np.concatenate([[0.0], np.asarray(curve, dtype=float)]))
    d = pd.to_datetime(pd.Series(dates))
    hit = pd.Series((daily > 0).astype(float))
    roll = hit.rolling(window).mean()

    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    ax.plot(d, roll * 100, lw=1.8, color=ACCENT)
    ax.axhline(50, color=INK, lw=1.0, ls="--")
    ax.fill_between(d, 50, roll * 100, where=(roll * 100) >= 50, color=UP, alpha=0.12)
    ax.fill_between(d, 50, roll * 100, where=(roll * 100) < 50, color=DOWN, alpha=0.12)
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.set_title(
        _t(f"{window}거래일 롤링 방향 적중률 (기준선 50%)",
           f"{window}-session Rolling Hit Rate (baseline 50%)"),
        loc="left",
    )
    fig.autofmt_xdate()
    fig.tight_layout()
    p = outdir / "w03_hit_rate.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def build_all(stats: dict, outdir: Path, dpi: int = 144) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    setup_style(dpi)
    paths = [
        chart_cum_spread(stats, outdir),
        chart_week_daily(stats, outdir),
        chart_hit_rate(stats, outdir),
    ]
    return [p for p in paths if p is not None]
