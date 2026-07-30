"""고정 스타일 차트 4종.

디자인 원칙: 매일 같은 4장을 같은 레이아웃으로 낸다. 형태는 고정이고 데이터만 바뀐다.
색은 6색 이내, 폰트 1종, 격자 최소. 정보를 늘리려고 요소를 추가하지 않는다.

한글 폰트: 시스템에 없으면 축 라벨이 네모로 깨진다. setup_style()이 사용 가능한
한글 폰트를 탐색하고, 없으면 라벨을 영문으로 자동 전환한다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

log = logging.getLogger(__name__)

UP = "#C0392B"      # 상승: 한국 관행상 적색
DOWN = "#1F6FB2"    # 하락: 청색
NEUTRAL = "#8A8F98"
INK = "#1A1A1A"
GRID = "#E3E5E8"
ACCENT = "#5B4B8A"

# 우선순위: macOS -> Windows -> 나눔 -> Noto.
# Noto Sans CJK 는 지역 변형(KR/JP/SC/TC)이 글리프를 공유하므로 JP 빌드만 있어도
# 한글이 정상 렌더링된다. Ubuntu 러너에는 CJK-JP만 깔리는 경우가 있어 폴백에 포함.
KOREAN_FONTS = ["AppleGothic", "Apple SD Gothic Neo", "Malgun Gothic",
                "NanumGothic", "NanumBarunGothic", "UnDotum",
                "Noto Sans CJK KR", "Noto Sans KR",
                "Noto Sans CJK JP", "Noto Sans CJK SC", "Noto Sans CJK TC"]
HAS_KOREAN = False


def setup_style(dpi: int = 144) -> bool:
    global HAS_KOREAN
    available = {f.name for f in fm.fontManager.ttflist}
    picked = next((f for f in KOREAN_FONTS if f in available), None)
    HAS_KOREAN = picked is not None
    if picked:
        plt.rcParams["font.family"] = picked
    else:
        log.warning("한글 폰트 없음 -> 차트 라벨을 영문으로 출력. "
                    "해결: apt-get install fonts-nanum 또는 fonts-noto-cjk")
    plt.rcParams.update({
        "figure.dpi": dpi,
        "savefig.dpi": dpi,
        "axes.unicode_minus": False,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "font.size": 10,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "legend.frameon": False,
    })
    return HAS_KOREAN


def _t(ko: str, en: str) -> str:
    return ko if HAS_KOREAN else en


def _bar_colors(vals) -> list[str]:
    return [UP if v > 0 else (DOWN if v < 0 else NEUTRAL) for v in vals]


def chart_sectors(sectors: pd.DataFrame, session, outdir: Path) -> Path | None:
    """1. 섹터 수익률 바"""
    if sectors is None or sectors.empty:
        return None
    d = sectors.sort_values("ret")
    labels = d["name"] if HAS_KOREAN else d["ticker"]
    fig, ax = plt.subplots(figsize=(8.3, 5.2))
    ax.barh(labels, d["ret"] * 100, color=_bar_colors(d["ret"]), height=0.66)
    ax.axvline(0, color=INK, lw=0.9)
    ax.set_xlabel("%")
    ax.set_title(_t(f"섹터별 일간 수익률 · {session:%Y-%m-%d}",
                    f"Sector Daily Returns · {session:%Y-%m-%d}"))
    ax.grid(axis="y", visible=False)
    for y, v in enumerate(d["ret"] * 100):
        ax.text(v + (0.03 if v >= 0 else -0.03), y, f"{v:+.2f}",
                va="center", ha="left" if v >= 0 else "right", fontsize=9)
    ax.margins(x=0.16)
    fig.tight_layout()
    p = outdir / "01_sectors.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_factors(factors: dict, session, outdir: Path) -> Path | None:
    """2. 팩터 수익률 바"""
    if not factors:
        return None
    names = {"mkt_rf": _t("시장", "MKT"), "smb": _t("규모", "SMB"), "hml": _t("가치", "HML"),
             "rmw": _t("수익성", "RMW"), "cma": _t("투자", "CMA"), "umd": _t("모멘텀", "UMD")}
    keys = [k for k in names if k in factors]
    vals = [factors[k] * 100 for k in keys]

    fig, ax = plt.subplots(figsize=(8.3, 4.0))
    ax.bar([names[k] for k in keys], vals, color=_bar_colors(vals), width=0.58)
    ax.axhline(0, color=INK, lw=0.9)
    ax.set_ylabel("%")
    ax.set_title(_t(f"팩터 수익률 (FF5 + 모멘텀) · {session:%Y-%m-%d}",
                    f"Factor Returns (FF5 + UMD) · {session:%Y-%m-%d}"))
    ax.grid(axis="x", visible=False)
    for i, v in enumerate(vals):
        ax.text(i, v + (0.02 if v >= 0 else -0.02), f"{v:+.2f}",
                ha="center", va="bottom" if v >= 0 else "top", fontsize=9)
    ax.margins(y=0.22)
    fig.tight_layout()
    p = outdir / "02_factors.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


# FRED 시리즈 ID를 그대로 범례에 쓰면 독자에게 아무 의미가 없다(BAMLH0A0HYM2).
# 1번 블록 표는 한글 라벨을 쓰므로 차트도 같은 어휘로 맞춘다.
MACRO_LABELS = {
    "DGS2": ("2년물", "2Y"),
    "DGS10": ("10년물", "10Y"),
    "BAMLH0A0HYM2": ("하이일드 OAS", "HY OAS"),
    "T10Y2Y": ("10Y-2Y", "10Y-2Y"),
    "VIXCLS": ("VIX", "VIX"),
    "T10YIE": ("기대인플레", "10Y BEI"),
    "DTWEXBGS": ("달러지수", "USD Index"),
    "DFF": ("EFFR", "EFFR"),
}


def macro_window(macro_wide: pd.DataFrame, session, lookback: int = 120) -> pd.DataFrame:
    """차트에 쓸 구간을 자른다. **관측이 있는 영업일만** 남긴다.

    macro는 달력일 인덱스다. tail(lookback)을 그대로 쓰면 주말이 포함된 달력
    120일(관측 30% 결측, 실제 ~82거래일)이 잡히고, 선이 매주 끊겨 파선처럼 보이며
    제목의 '거래일'이 거짓이 된다(2026-07-30 실측). 잘라내는 순서가 중요하다 --
    반드시 결측·주말을 **먼저** 버리고 그 다음에 tail을 취해야 한다.
    """
    if macro_wide is None or macro_wide.empty:
        return pd.DataFrame()
    w = macro_wide[macro_wide.index <= pd.Timestamp(session)]
    w = w.dropna(how="all")
    w = w[w.index.dayofweek < 5]
    return w.tail(lookback)


def chart_macro(macro_wide: pd.DataFrame, session, outdir: Path, lookback: int = 120) -> Path | None:
    """3. 금리·크레딧·변동성 3패널

    주의 두 가지:
    1. **주말·휴일 행을 먼저 버린다.** macro는 달력일 인덱스라 tail(120)을 그대로
       쓰면 30% 이상이 NaN인 120 달력일(≈82거래일)이 잡히고, 선이 매주 끊겨 파선처럼
       보인다. 제목의 '거래일'도 거짓이 된다(2026-07-30 실측).
    2. **10Y-2Y를 HY OAS와 같은 축에 두지 않는다.** 레벨이 0.35 vs 2.8이라 스프레드가
       납작하게 깔려 변화가 전혀 안 보인다. 오른쪽 축으로 분리한다.
    """
    if macro_wide is None or macro_wide.empty:
        return None
    w = macro_window(macro_wide, session, lookback)
    if w.empty:
        return None

    panels = [
        (["DGS2", "DGS10"], _t("미국채 금리 (%)", "UST Yields (%)"), None),
        (["BAMLH0A0HYM2"], _t("하이일드 OAS (%p)", "HY OAS (%p)"), "T10Y2Y"),
        (["VIXCLS"], _t("VIX", "VIX"), None),
    ]
    panels = [(c, t, r) for c, t, r in panels if any(x in w.columns for x in c)]
    if not panels:
        return None

    def _lab(col: str) -> str:
        ko, en = MACRO_LABELS.get(col, (col, col))
        return _t(ko, en)

    fig, axes = plt.subplots(len(panels), 1, figsize=(8.3, 2.1 * len(panels)), sharex=True)
    axes = np.atleast_1d(axes)
    palette = [ACCENT, UP, DOWN]
    for ax, (cols, title, right_col) in zip(axes, panels):
        handles = []
        for i, c in enumerate(cols):
            if c in w.columns and w[c].notna().any():
                s = w[c].dropna()     # 단일 결측에서 선이 끊기지 않게 한다
                ln, = ax.plot(s.index, s.values, lw=1.5,
                              color=palette[i % len(palette)], label=_lab(c))
                handles.append(ln)
        if right_col and right_col in w.columns and w[right_col].notna().any():
            ax2 = ax.twinx()
            s = w[right_col].dropna()
            ln, = ax2.plot(s.index, s.values, lw=1.5, color=UP, label=_lab(right_col))
            handles.append(ln)
            ax2.set_ylabel(_lab(right_col), fontsize=8.5, color=UP)
            ax2.tick_params(axis="y", labelsize=8, colors=UP)
            ax2.grid(False)           # 격자 두 겹은 읽기를 방해한다
        ax.set_title(title, fontsize=10.5, loc="left")
        if len(handles) > 1:
            # 범례를 데이터 위에 두면 선을 가리고, 제목과 같은 왼쪽에 두면 제목을 덮는다.
            # 제목은 왼쪽, 범례는 같은 행 오른쪽으로 나눈다.
            ax.legend(handles, [h.get_label() for h in handles], fontsize=8, ncol=2,
                      loc="lower right", bbox_to_anchor=(1.0, 1.0), frameon=False,
                      handlelength=1.4, columnspacing=1.2)

    # x축 라벨이 서로 겹쳐 읽을 수 없었다. 눈금 수를 제한하고 기울인다.
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    for lb in axes[-1].get_xticklabels():
        lb.set_rotation(20)
        lb.set_horizontalalignment("right")
    axes[-1].set_xlabel("")
    fig.suptitle(_t(f"매크로 신호 · 최근 {len(w)}거래일", f"Macro Signals · last {len(w)} sessions"),
                 fontsize=13, fontweight="bold", y=1.0)
    fig.tight_layout()
    p = outdir / "03_macro.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_residuals(resid: pd.DataFrame, session, outdir: Path, sigma_cut: float = 2.0) -> Path | None:
    """4. 잔차 분포 + 이례치 라벨"""
    if resid is None or resid.empty or "z" not in resid.columns:
        return None
    d = resid.dropna(subset=["z", "residual"]).copy()
    if d.empty:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.4),
                                   gridspec_kw={"width_ratios": [1, 1.35]})

    ax1.hist(d["residual"] * 100, bins=45, color=NEUTRAL, alpha=0.75, edgecolor="white", lw=0.4)
    ax1.axvline(0, color=INK, lw=0.9)
    ax1.set_xlabel("%")
    ax1.set_title(_t("잔차 분포", "Residual Distribution"), fontsize=11, loc="left")
    ax1.grid(axis="x", visible=False)

    out = pd.concat([d.nlargest(8, "z"), d.nsmallest(8, "z")]).drop_duplicates("ticker")
    out = out.sort_values("z")
    ax2.barh(out["ticker"], out["z"], color=_bar_colors(out["z"]), height=0.68)
    ax2.axvline(sigma_cut, color=INK, lw=0.8, ls="--")
    ax2.axvline(-sigma_cut, color=INK, lw=0.8, ls="--")
    ax2.axvline(0, color=INK, lw=0.9)
    ax2.set_xlabel(_t("표준화 잔차 (σ)", "Standardized residual (σ)"))
    ax2.set_title(_t("이례치 상하위", "Residual Outliers"), fontsize=11, loc="left")
    ax2.grid(axis="y", visible=False)
    ax2.tick_params(labelsize=8.5)

    fig.suptitle(_t(f"위험조정 잔차 · {session:%Y-%m-%d} (n={len(d)})",
                    f"Risk-adjusted Residuals · {session:%Y-%m-%d} (n={len(d)})"),
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    p = outdir / "04_residuals.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def build_all(ctx: dict, outdir: Path, dpi: int = 144) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    setup_style(dpi)
    session = pd.Timestamp(ctx["session"])
    paths = [
        chart_sectors(ctx.get("sectors_df"), session, outdir),
        chart_factors(ctx.get("factors", {}), session, outdir),
        chart_macro(ctx.get("macro_wide"), session, outdir),
        chart_residuals(ctx.get("resid_df"), session, outdir),
    ]
    return [p for p in paths if p is not None]
