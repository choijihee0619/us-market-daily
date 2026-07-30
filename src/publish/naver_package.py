"""네이버 블로그 요약본 (유입 채널).

전문을 옮기지 않는다. 세 가지 이유 때문이다.

1) 중복 콘텐츠. 같은 글이 두 도메인에 있으면 검색엔진이 어느 쪽을 정본으로 볼지
   판단해야 하고, 보통 도메인 권위가 높은 쪽이 이긴다. 수익은 티스토리에서 나오므로
   정본을 티스토리에 두고 네이버에는 요약만 둔다.
2) 네이버는 외부 링크가 많은 글의 노출을 낮추는 경향이 있다. 링크를 1개로 제한한다.
   [검증 필요 — 공식 문서화된 규칙이 아니라 경험칙이다]
3) 요약본은 3분이면 붙여넣는다. 매일 하는 일은 짧아야 지속된다.

구성: 세 줄 요약 → 지수 표 → 어제 채점 결과 → 원문 링크 1개.
스크롤 3~4화면을 넘기지 않는다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

from .common import default_tags, headline_bullets, slug

INSTRUCTIONS = """네이버 블로그 게시 절차 (약 2분)

1. title.txt 제목을 붙여넣는다.
2. 에디터에서 post.txt 내용을 붙여넣는다.
   (네이버 스마트에디터는 HTML 붙여넣기를 상당 부분 정규화하므로 요약본은
    평문으로 넣고 표만 에디터의 '표' 기능으로 만드는 편이 깨지지 않는다.)
3. images/ 에서 1~2장만 첨부한다. 전부 넣지 않는다 -- 요약본이다.
4. 맨 아래 원문 링크를 넣는다. 링크는 1개만.
5. tags.txt 태그 입력 -> 공개 -> 예약 07:10.
   (티스토리 07:00보다 10분 뒤에 올려 원문이 먼저 색인되게 한다.)

주의
- 전문을 복사해 오지 말 것. 중복 콘텐츠가 되고, 수익이 나는 쪽(티스토리)의
  검색 순위를 네이버 글이 잡아먹는다.
- 네이버는 외부 링크가 많은 글의 노출을 낮추는 경향이 있다. 링크는 1개로 유지한다.
- 매일 같은 문장 골격이 반복되면 유사문서로 잡힐 수 있다. 세 줄 요약 중
  한 줄은 직접 쓰는 것을 권한다.
"""


def build_summary(ctx: dict, title: str, canonical_url: str | None = None) -> str:
    session = pd.Timestamp(ctx["session"])
    L: list[str] = []
    A = L.append

    A(title)
    A("")
    A(f"대상 거래일 {session:%Y년 %m월 %d일} (미국 동부시간 기준)")
    A("")

    for b in headline_bullets(ctx, 3):
        A(f"· {b}")
    A("")

    bm = ctx.get("benchmarks", {})
    if bm:
        A("[ 주요 지수 ]")
        for t, label in [("SPY", "S&P 500"), ("QQQ", "나스닥 100"),
                         ("IWM", "러셀 2000"), ("DIA", "다우 30")]:
            if t in bm:
                A(f"{label}   {bm[t]*100:+.2f}%")
        A("")

    sect = ctx.get("sectors", [])
    if sect:
        A("[ 섹터 ]")
        A(f"최상위  {sect[0]['name']}  {sect[0]['ret']*100:+.2f}%")
        A(f"최하위  {sect[-1]['name']}  {sect[-1]['ret']*100:+.2f}%")
        A("")

    mac = ctx.get("macro", {})
    if mac:
        A("[ 금리·변동성 ]")
        LBL = {"DGS2": "미국채 2년", "DGS10": "미국채 10년",
               "VIXCLS": "VIX", "BAMLH0A0HYM2": "하이일드 스프레드"}
        for k, label in LBL.items():
            if k in mac:
                m = mac[k]
                chg = f"{m['change']:+.0f}bp" if m["unit"] == "bp" else f"{m['change']:+.2f}"
                A(f"{label}   {m['level']:.2f}   ({chg})")
        A("")

    sc = ctx.get("scorecard", {})
    if sc.get("available"):
        A("[ 어제 신호 채점 ]")
        A("직전 거래일 뉴스 감성 상위 20% 종목과 하위 20% 종목의")
        A("오늘 실현 초과수익률 차이입니다.")
        A("")
        A(f"스프레드  {sc['spread_bp']:+.1f}bp  "
          f"({'예측 방향 일치' if sc['hit'] else '예측 방향 불일치'})")
        cum = ctx.get("scorecard_cum", {})
        if cum.get("n_days", 0) >= 5:
            A(f"누적 {cum['n_days']}거래일 방향 적중률  {cum['hit_rate']*100:.0f}%")
        A("")
        A("맞은 날과 틀린 날을 모두 그대로 남깁니다.")
        A("")

    cs = ctx.get("cross_section", {})
    if cs.get("n"):
        A("[ 설명되지 않은 움직임 ]")
        A(f"위험모형으로 설명되지 않는 잔차가 ±2σ를 벗어난 종목은 "
          f"{cs['n_up_outlier']+cs['n_down_outlier']}개입니다 "
          f"(분석 대상 {cs['n']}개).")
        top = cs.get("top", [])[:3]
        if top:
            A("상방: " + ", ".join(f"{r['ticker']} {r['z']:+.1f}σ" for r in top))
        bot = cs.get("bottom", [])[:3]
        if bot:
            A("하방: " + ", ".join(f"{r['ticker']} {r['z']:+.1f}σ" for r in bot))
        A("")

    A("─" * 22)
    A("")
    A("종목별 잔차, 뉴스 토픽 회귀 계수, 사용한 모형과 코드는")
    A("전체 리포트에 있습니다.")
    if canonical_url:
        A("")
        A(f"전체 리포트 → {canonical_url}")
    A("")
    A("본 글은 공개 데이터를 자동 수집·분석한 연구 기록이며 투자자문이 아닙니다.")
    A("특정 종목의 매수·매도를 권유하지 않습니다.")
    return "\n".join(L)


def write_package(session, title: str, ctx: dict, chart_paths: list[Path],
                  out_root: Path, canonical_url: str | None = None) -> Path:
    pkg = Path(out_root) / slug(session) / "naver"
    (pkg / "images").mkdir(parents=True, exist_ok=True)

    text = build_summary(ctx, title, canonical_url)
    (pkg / "title.txt").write_text(title, encoding="utf-8")
    (pkg / "post.txt").write_text(text, encoding="utf-8")
    (pkg / "README.txt").write_text(INSTRUCTIONS, encoding="utf-8")
    (pkg / "tags.txt").write_text(", ".join(default_tags(session)), encoding="utf-8")

    # 요약본이므로 대표 차트 2장만
    for p in [Path(x) for x in chart_paths][:2]:
        if p.exists():
            shutil.copy(p, pkg / "images" / p.name)
    return pkg
