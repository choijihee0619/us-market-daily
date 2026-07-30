"""발행 어댑터 공통 유틸.

세 채널 모두 '붙여넣기 직전까지' 자동화한다는 점은 같다.
  - 네이버: 글쓰기 API 2020-05-06 종료
  - 티스토리: Open API 2024년 2월 완전 종료 (파일첨부 -> 글 -> 댓글 순 순차 종료)
  - GitHub: 유일하게 완전 자동 (git push)
따라서 수동 작업을 최소화하는 게 설계 목표다. 목표 소요 5분.
"""
from __future__ import annotations

import pandas as pd


def headline_bullets(ctx: dict, n: int = 3) -> list[str]:
    """첫 화면 이탈을 막는 핵심 요약 n줄. 숫자가 반드시 들어간다."""
    out: list[str] = []

    bm = ctx.get("benchmarks", {})
    sect = ctx.get("sectors", [])
    if bm.get("SPY") is not None:
        s = f"S&P 500 {bm['SPY']*100:+.2f}%"
        if bm.get("QQQ") is not None:
            s += f", 나스닥 100 {bm['QQQ']*100:+.2f}%"
        if sect:
            s += f". 11개 섹터 중 {sum(1 for x in sect if x['ret'] > 0)}개 상승"
        out.append(s + ".")

    mac = ctx.get("macro", {})
    if "DGS10" in mac:
        s = f"10년물 {mac['DGS10']['change']:+.0f}bp → {mac['DGS10']['level']:.2f}%"
        if "VIXCLS" in mac:
            s += f", VIX {mac['VIXCLS']['level']:.1f}"
        out.append(s + ".")

    tr = ctx.get("topic_regression", {})
    cs = ctx.get("cross_section", {})
    if tr.get("r2") is not None and cs.get("n"):
        top = next(iter(tr.get("coef", {})), None)
        s = f"뉴스 토픽이 설명한 잔차 분산 {tr['r2']*100:.0f}%"
        if top:
            s += f", 최대 기여 토픽은 {top}"
        s += f". ±2σ 이탈 {cs['n_up_outlier']+cs['n_down_outlier']}종목"
        out.append(s + ".")

    sc = ctx.get("scorecard", {})
    if sc.get("available"):
        out.append(f"직전 신호 5분위 스프레드 {sc['spread_bp']:+.1f}bp "
                   f"({'방향 일치' if sc['hit'] else '방향 불일치'}).")

    return out[:n]


def default_tags(session) -> list[str]:
    return ["미국주식", "미국증시", "매크로", "팩터투자", "퀀트",
            "데이터분석", "시장분석", pd.Timestamp(session).strftime("%Y%m%d")]


def slug(session) -> str:
    return pd.Timestamp(session).strftime("%Y-%m-%d")
