"""주간 회고 포스팅 빌더.

구조를 '가설 → 데이터 → 결과 → 판정 → 한계 → 다음 가설' 6단으로 고정한다.
이유는 두 가지다.

1) 검색 가치. 일간 글은 하루 지나면 죽지만 "뉴스 감성이 익일 수익률을 설명하는가"
   같은 글은 계속 검색된다. 애드센스 수익은 여기서 나온다.
2) 반증 가능성. 가설을 먼저 명시하고 나중에 결과를 보는 순서를 강제하면
   사후 서사(HARKing)를 피할 수 있다. 틀린 주도 그대로 남긴다.
"""
from __future__ import annotations

import math

import pandas as pd

from ..process.weekly_stats import T_HURDLE, verdict

HYPOTHESIS = (
    "직전 거래일에 보도된 뉴스의 감성(novelty 가중)이 익일 초과수익률의 "
    "횡단면 차이를 설명한다."
)
NULL = "설명하지 않는다. 감성 상위 분위와 하위 분위의 초과수익률 차이는 0이다."


def _f(x, d=1, suffix=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:+.{d}f}{suffix}"


def make_title(stats: dict) -> str:
    s, e = pd.Timestamp(stats["start"]), pd.Timestamp(stats["end"])
    bits = [f"{s:%m/%d}~{e:%m/%d} 주간 검증"]
    if stats.get("n_week"):
        bits.append(f"적중 {stats['week_hits']}/{stats['n_week']}")
    t = stats.get("cum_t")
    if t is not None and not math.isnan(t):
        bits.append(f"누적 t={t:.2f}")
    if stats.get("cum_mean_bp") is not None:
        bits.append(f"일평균 {stats['cum_mean_bp']:+.1f}bp")
    return " | ".join(bits)


def build_markdown(stats: dict, ctx: dict, chart_files: list[str], cfg) -> str:
    s, e = pd.Timestamp(stats["start"]), pd.Timestamp(stats["end"])
    L: list[str] = []
    A = L.append

    A(f"# {make_title(stats)}")
    A("")
    A(f"> 검증 구간 {s:%Y-%m-%d} ~ {e:%Y-%m-%d} (ET) · "
      f"이번 주 {stats.get('n_week',0)}거래일 · 누적 {stats.get('n_total',0)}거래일")
    A("")

    # --- 1. 가설 ---
    A("## 1. 가설")
    A("")
    A(f"**H1.** {HYPOTHESIS}")
    A("")
    A(f"**H0.** {NULL}")
    A("")
    A("검정 방법은 다음과 같다. 매 거래일 뉴스 감성 점수로 종목을 5분위로 나누고, "
      "최상위 분위와 최하위 분위의 **위험조정 잔차** 평균 차이를 스프레드로 정의한다. "
      "잔차는 FF5+모멘텀 모형에서 나온 값이라 시장·규모·가치·수익성·투자·모멘텀 "
      "노출은 이미 제거되어 있다.")
    A("")
    A("일별 스프레드 시계열의 평균이 0과 유의하게 다른지를 본다. "
      f"표준오차는 Newey-West(lag {stats.get('lags',5)})로 낸다. "
      "일별 스프레드에 자기상관이 있어 OLS 표준오차를 쓰면 t가 부풀려지기 때문이다.")
    A("")

    # --- 2. 데이터 ---
    A("## 2. 데이터")
    A("")
    A("| 항목 | 내용 |")
    A("|---|---|")
    A(f"| 검증 구간 | {s:%Y-%m-%d} ~ {e:%Y-%m-%d} |")
    A(f"| 이번 주 관측 | {stats.get('n_week',0)}거래일 |")
    A(f"| 누적 관측 | {stats.get('n_total',0)}거래일 |")
    A(f"| 위험모형 | {ctx.get('spec','ff5_umd')} · 롤링 {ctx.get('beta_window',250)}거래일 |")
    A("| 감성 | Loughran-McDonald 금융 사전 · novelty 가중 |")
    A("| 포트폴리오 | 5분위 동일가중 · 거래비용 미반영 |")
    A("")

    # --- 3. 결과 ---
    A("## 3. 결과")
    A("")
    wd = stats.get("week_daily") or []
    if wd:
        A("이번 주 일별 스프레드:")
        A("")
        A("| 거래일 | 스프레드 | 방향 |")
        A("|---|---|---|")
        for r in wd:
            A(f"| {pd.Timestamp(r['date']):%m-%d (%a)} | {_f(r['bp'], 1, 'bp')} | "
              f"{'일치' if r['hit'] else '불일치'} |")
        A("")
        A(f"이번 주 평균 {_f(stats.get('week_mean_bp'),1,'bp')}, "
          f"방향 적중 {stats['week_hits']}/{stats['n_week']}일.")
        if stats.get("week_best") and stats.get("week_worst"):
            A(f"최고 {pd.Timestamp(stats['week_best']['date']):%m-%d} "
              f"{_f(stats['week_best']['bp'],1,'bp')}, "
              f"최저 {pd.Timestamp(stats['week_worst']['date']):%m-%d} "
              f"{_f(stats['week_worst']['bp'],1,'bp')}.")
        A("")

    if chart_files:
        A(f"![]({chart_files[0]})")
        A("")

    A("누적 통계:")
    A("")
    A("| 지표 | 값 |")
    A("|---|---|")
    A(f"| 관측 | {stats.get('n_total',0)}거래일 |")
    A(f"| 일평균 스프레드 | {_f(stats.get('cum_mean_bp'),2,'bp')} |")
    A(f"| 표준편차 | {stats.get('cum_sd_bp',float('nan')):.2f}bp |")
    A(f"| Newey-West SE | {stats.get('cum_se_bp',float('nan')):.2f}bp |")
    A(f"| **t-통계량** | **{stats.get('cum_t',float('nan')):.2f}** |")
    A(f"| 방향 적중률 | {stats.get('cum_hit_rate',0)*100:.1f}% "
      f"({stats.get('cum_hits',0)}/{stats.get('n_total',0)}) |")
    A(f"| 이항검정 p | {stats.get('cum_binom_p',float('nan')):.3f} |")
    if stats.get("ann_sharpe") is not None:
        A(f"| 연율 Sharpe (참고) | {stats['ann_sharpe']:.2f} |")
    A("")

    if len(chart_files) > 1:
        A(f"![]({chart_files[1]})")
        A("")
    if len(chart_files) > 2:
        A(f"![]({chart_files[2]})")
        A("")

    fac = ctx.get("weekly_factors", {})
    if fac:
        NM = {"mkt_rf": "시장", "smb": "규모", "hml": "가치",
              "rmw": "수익성", "cma": "투자", "umd": "모멘텀"}
        A("주간 누적 팩터 수익률:")
        A("")
        A("| 팩터 | 주간 |")
        A("|---|---|")
        for k, v in sorted(fac.items(), key=lambda kv: -abs(kv[1])):
            A(f"| {NM.get(k,k)} | {v*100:+.2f}% |")
        A("")

    ro = ctx.get("recurring_outliers")
    if ro is not None and not ro.empty:
        A("주중 2회 이상 ±2σ를 벗어난 종목:")
        A("")
        A("| 종목 | 횟수 | 평균 z | 최대 |z| | 누적 잔차 |")
        A("|---|---|---|---|---|")
        for _, r in ro.iterrows():
            A(f"| {r['ticker']} | {int(r['n'])} | {r['mean_z']:+.2f} | "
              f"{r['max_abs_z']:.2f} | {r['cum_resid_bp']:+.0f}bp |")
        A("")
        A("반복 이탈은 두 가지 중 하나다. 실제 사건이 진행 중이거나, "
          "그 종목의 베타 추정이 잘못됐거나. 후자라면 뉴스가 아니라 모형 문제다.")
        A("")

    # --- 4. 판정 ---
    A("## 4. 판정")
    A("")
    label, reason = verdict(stats)
    A(f"**{label}**")
    A("")
    A(reason)
    A("")
    A(f"판정 기준을 관행적인 |t| > 2.0 이 아니라 **|t| > {T_HURDLE}** 으로 둔 이유가 있다. "
      "Harvey, Liu & Zhu(2016)는 지금까지 발표된 수백 개의 팩터를 놓고 보면 "
      "다중검정 때문에 2.0 기준으로는 우연히 유의한 결과가 대량 생산된다고 지적했다. "
      "매주 여러 스펙을 들여다보는 이 프로젝트는 정확히 그 함정에 취약하다.")
    A("")

    # --- 5. 한계 ---
    A("## 5. 한계")
    A("")
    A("- **거래비용 미반영.** 5분위 롱숏은 매일 리밸런싱을 전제한다. "
      "실제로는 스프레드·수수료·시장충격이 붙는다. 집행 가능한 전략 수익률이 아니다.")
    A("- **뉴스 커버리지 편향.** 수집원이 대형주에 편중되어 있다. "
      "중소형주는 감성 점수 자체가 결측이거나 기사 1~2건에 좌우된다.")
    A("- **generated regressand.** 잔차가 추정된 베타에서 나온 값이라 "
      "2단계 추정오차가 표준오차에 반영되지 않았다. Shanken 보정이나 "
      "block bootstrap이 필요하다.")
    A("- **생존편향.** 현재 지수 구성종목을 쓴다. 편입·편출 시점을 반영하면 결과가 달라질 수 있다.")
    A(f"- **표본.** 누적 {stats.get('n_total',0)}거래일은 시장 국면 하나를 겨우 덮는 길이다. "
      "국면이 바뀌면 결론도 바뀔 수 있다.")
    A("")

    # --- 6. 다음 가설 ---
    A("## 6. 다음 주 가설")
    A("")
    nxt = ctx.get("next_hypothesis") or (
        "감성 효과가 시장 국면에 따라 달라지는지 본다. VIX 상위 3분위 구간과 "
        "하위 3분위 구간으로 나눠 스프레드를 각각 추정한다. "
        "고변동성 구간에서 감성 반응이 더 크다면, 그건 정보 효과가 아니라 "
        "유동성 프리미엄일 가능성이 있다."
    )
    A(nxt)
    A("")
    A("---")
    A("")
    A(f"_{cfg.get_path('disclaimer','').strip()}_")
    A("")
    A("데이터와 코드는 전부 공개되어 있다. 맞은 주와 틀린 주를 모두 그대로 남긴다.")
    rev = ctx.get("git_rev")
    if rev:
        A("")
        A(f"생성 커밋: `{rev}`")
    return "\n".join(L)
