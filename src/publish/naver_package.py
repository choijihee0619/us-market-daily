"""네이버 블로그 — 미국 시장 뉴스 분석·인사이트 리포트 (평문).

**티스토리 요약본이 아니다.** 컨셉을 분리했다.

왜 바꿨나:
요약본 방식은 중복 콘텐츠를 피하려고 원문을 줄인 것이라, 네이버 독자 입장에서는
"티스토리 글의 열화판"이었다. 클릭할 이유가 약하고, 매일 같은 골격이 반복되어
유사문서로 잡힐 위험도 있었다.

지금 구조는 **소재 자체가 다르다.**
  - 티스토리: 팩터·잔차 중심의 계량 기록 (숫자가 주인공)
  - 네이버:   그날 미국 시장에서 무슨 뉴스가 있었고 어떤 종목이 반응했는가
              (뉴스가 주인공, 잔차는 근거로만 인용)
같은 데이터를 쓰지만 서술 축이 달라 문장이 겹치지 않는다. 중복 콘텐츠 위험을
줄이면서 네이버 쪽에도 읽을 이유를 만든다.

형식은 평문(txt)을 유지한다. 네이버 스마트에디터는 붙여넣은 HTML을 상당 부분
정규화해서 표·스타일이 깨진다. 평문이 가장 안정적이다.

외부 링크는 1개로 유지한다. 네이버가 외부 링크 많은 글의 노출을 낮추는 경향이
있다고 알려져 있다.  [검증 필요 -- 공식 문서화된 규칙이 아니라 경험칙이다]
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..storage import as_list
from .common import default_tags, slug

INSTRUCTIONS = """네이버 블로그 게시 절차 (약 2분)

1. title.txt 제목을 붙여넣는다.
2. post.txt 내용을 붙여넣는다. 평문이라 그대로 들어간다.
3. 이미지 1~2장 첨부 (선택). 파일은 assets/{날짜}/ 에 있다.
   (패키지 안에 따로 복사하지 않는다 -- assets/ 와 중복이라 저장소만 커진다.)
4. tags.txt 태그 입력 -> 공개 -> 예약 07:10.
   (티스토리 07:00보다 10분 뒤에 올려 원문이 먼저 색인되게 한다.)

이 글의 컨셉
- 티스토리 글의 요약본이 아니다. **뉴스 중심 리포트**다.
  티스토리는 팩터·잔차가 주인공이고, 여기는 뉴스가 주인공이다.
- 그래서 문장이 겹치지 않는다. 중복 콘텐츠 위험이 낮다.

주의
- 전문을 복사해 오지 말 것. 수익이 나는 쪽(티스토리) 순위를 잡아먹는다.
- 외부 링크는 1개만 유지한다.
- 마지막 '오늘의 정리' 한 줄은 직접 고쳐 쓰는 것을 권한다. 매일 같은 골격이
  반복되면 유사문서로 잡힐 수 있다.
"""

TOPIC_NOTE = {
    "통화정책": "금리 경로에 대한 기대가 바뀌면 거의 모든 종목의 할인율이 함께 움직인다",
    "인플레이션": "물가 지표는 연준 정책 기대를 통해 금리와 주식 양쪽에 반영된다",
    "고용": "고용은 연준이 가장 무겁게 보는 지표다",
    "실적": "개별 기업 고유의 재료다. 그날 그 종목만 크게 움직이는 대표적 이유",
    "가이던스": "실적 자체보다 향후 전망 수정이 주가를 더 크게 움직이는 경우가 많다",
    "M&A": "인수 대상 기업은 프리미엄이, 인수 기업은 부담이 반영되는 경향이 있다",
    "규제정책": "관세·소송·제재는 산업 전체에 동시에 걸린다",
    "지정학": "지정학 재료는 에너지·방산 같은 특정 섹터로 먼저 전달된다",
    "공급망": "생산 차질은 매출보다 마진에 먼저 나타난다",
    "AI/데이터센터투자": "설비투자 계획 변화는 반도체·전력·냉각 등 공급망 전체로 퍼진다",
    "에너지": "유가는 에너지 섹터 이익과 동시에 물가 기대에도 반영된다",
    "소비": "미국 GDP의 3분의 2가 소비다",
}


def _news_digest(news_win: pd.DataFrame, topics: list[str], limit: int = 3,
                 universe: set[str] | None = None) -> list[dict]:
    """토픽별 기사 수와 대표 헤드라인.

    대표 헤드라인 선정 기준이 중요하다. **novelty만으로 고르면 안 된다.**
    novelty는 '처음 보는 문장'을 높게 주므로 무명 기업의 소액 자금조달 공시 같은
    기사가 1위로 올라온다(실측: 통화정책 대표 헤드라인이 'StratGrid $1.75M 펀딩').
    독자가 아는 회사가 나와야 읽을 이유가 생긴다.

    그래서 **분석 유니버스(S&P 500) 종목이 태그된 기사를 먼저** 고르고, 그 안에서
    novelty 순으로 자른다. 유니버스 기사가 없을 때만 전체에서 고른다.
    """
    if news_win is None or news_win.empty or "topic" not in news_win.columns:
        return []
    rows = []
    for t in topics:
        m = news_win["topic"].apply(lambda v, t=t: t in as_list(v))
        sub = news_win[m]
        if sub.empty:
            continue
        pick = sub
        if universe and "tickers" in sub.columns:
            in_uni = sub["tickers"].apply(
                lambda v: any(x in universe for x in as_list(v)))
            if in_uni.any():
                pick = sub[in_uni]
        pick = pick.sort_values("novelty", ascending=False)
        rows.append({
            "topic": t,
            "n": int(len(sub)),
            "headlines": [str(h) for h in pick["headline"].head(limit)],
            "tickers": [", ".join(as_list(v)[:3]) for v in pick["tickers"].head(limit)]
                       if "tickers" in pick.columns else [],
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def build_report(ctx: dict, title: str, canonical_url: str | None = None,
                 news_win: pd.DataFrame | None = None,
                 topics: list[str] | None = None,
                 insight: str | None = None,
                 universe: set[str] | None = None) -> str:
    session = pd.Timestamp(ctx["session"])
    L: list[str] = []
    A = L.append

    A(f"[{session:%Y년 %m월 %d일} 미국장] 뉴스로 보는 하루")
    A("")
    A("미국 시장에서 그날 실제로 보도된 뉴스를 모아 정리한 기록입니다.")
    A("전망이나 매매 권유가 아니라, 무슨 일이 있었고 어떤 종목이 반응했는지를")
    A("숫자와 함께 남깁니다.")
    A("")

    # ---------------------------------------------- 1. 오늘의 뉴스 지형
    digest = _news_digest(news_win, topics or [], limit=2, universe=universe)
    if digest:
        A("─" * 24)
        A("1. 오늘 뉴스는 어디에 몰렸나")
        A("─" * 24)
        A("")
        # 주제별 건수를 더하면 안 된다. 한 기사가 여러 주제에 걸리므로 합계가
        # 실제 기사 수보다 커진다(실측: 창 455건인데 합계 2113건으로 표시됐다).
        n_articles = int(len(news_win)) if news_win is not None else 0
        A(f"이날 수집한 기사 {n_articles}건을 주제별로 나눴습니다.")
        A("(한 기사가 여러 주제에 걸치는 경우가 있어 아래 합계는 전체 건수와 다릅니다.)")
        A("")
        for d in digest[:5]:
            share = d["n"] / n_articles * 100 if n_articles else 0
            A(f"■ {d['topic']}  기사 {d['n']}건 (전체의 {share:.0f}%)")
            note = TOPIC_NOTE.get(d["topic"])
            if note:
                A(f"   → {note}")
            tks = d.get("tickers") or []
            for i, h in enumerate(d["headlines"]):
                tk = tks[i] if i < len(tks) and tks[i] else ""
                A(f"   · {h[:74]}" + (f"  [{tk}]" if tk else ""))
            A("")

    # ------------------------------------- 2. 뉴스가 있었던 종목의 움직임
    cs = ctx.get("cross_section", {})
    news_map = ctx.get("outlier_news", {})
    src_map = ctx.get("outlier_news_source", {})
    matched = [r for r in (cs.get("top", []) + cs.get("bottom", []))
               if r["ticker"] in news_map]
    if matched:
        A("─" * 24)
        A("2. 뉴스가 있었던 종목")
        A("─" * 24)
        A("")
        A("그날 유난히 크게 움직인 종목 중 관련 보도가 확인된 경우입니다.")
        A("괄호 안 숫자는 시장 전체 움직임을 걷어낸 뒤 남은 변동폭입니다.")
        A("")
        for r in matched[:8]:
            name = r.get("name", r["ticker"])
            sec = r.get("sector", "")
            head = news_map.get(r["ticker"], "")[:70]
            src = src_map.get(r["ticker"], "")
            A(f"■ {name} ({r['ticker']}) · {sec}")
            A(f"   {r['ret']*100:+.2f}%  (시장 요인 제거 후 {r['residual']*100:+.2f}%)")
            A(f"   {head}" + (f"  [{src}]" if src else ""))
            A("")

    # ------------------------------------------- 3. 뉴스가 없던 큰 움직임
    unmatched = [r for r in (cs.get("top", []) + cs.get("bottom", []))
                 if r["ticker"] not in news_map]
    if unmatched:
        A("─" * 24)
        A("3. 뉴스를 찾지 못한 움직임")
        A("─" * 24)
        A("")
        A("크게 움직였는데 수집 범위 안에서 관련 보도를 찾지 못한 종목입니다.")
        A("뉴스가 없었다는 뜻이 아니라, 무료 수집 범위에 안 들어왔다는 뜻입니다.")
        A("이 구분을 흐리지 않으려고 따로 적습니다.")
        A("")
        A("  " + ", ".join(f"{r.get('name', r['ticker'])}({r['ticker']}) "
                           f"{r['ret']*100:+.1f}%" for r in unmatched[:6]))
        A("")

    # ------------------------------------------------------- 4. 인사이트
    A("─" * 24)
    A("4. 오늘의 정리")
    A("─" * 24)
    A("")
    if insight:
        for line in insight.strip().split("\n"):
            if line.strip():
                A(line.strip())
        A("")

    bm = ctx.get("benchmarks", {})
    if bm.get("SPY") is not None:
        sect = ctx.get("sectors", [])
        A(f"참고로 그날 S&P 500은 {bm['SPY']*100:+.2f}%였습니다.")
        if sect:
            A(f"섹터는 {sect[0]['name']} {sect[0]['ret']*100:+.2f}%가 가장 높았고 "
              f"{sect[-1]['name']} {sect[-1]['ret']*100:+.2f}%가 가장 낮았습니다.")
        A("")

    # ------------------------------------------------------------ 마무리
    A("─" * 24)
    A("")
    A("이 글은 뉴스 중심으로 정리한 것입니다.")
    A("종목별 초과수익 계산 방식, 뉴스 주제 회귀 분석, 전날 신호 채점 결과는")
    A("전체 리포트에 있습니다.")
    if canonical_url:
        A("")
        A(f"전체 리포트 → {canonical_url}")
    A("")
    A("본 글은 공개 데이터를 자동 수집·분석한 연구 기록이며 투자자문이 아닙니다.")
    A("특정 종목의 매수·매도를 권유하지 않습니다.")
    return "\n".join(L)


def write_package(session, title: str, ctx: dict, chart_paths: list[Path],
                  out_root: Path, canonical_url: str | None = None,
                  news_win: pd.DataFrame | None = None,
                  topics: list[str] | None = None,
                  insight: str | None = None,
                  universe: set[str] | None = None) -> Path:
    pkg = Path(out_root) / slug(session) / "naver"
    pkg.mkdir(parents=True, exist_ok=True)

    text = build_report(ctx, title, canonical_url, news_win, topics, insight, universe)
    news_title = f"[{pd.Timestamp(session):%m/%d} 미국장] 뉴스로 보는 하루"
    (pkg / "title.txt").write_text(news_title, encoding="utf-8")
    (pkg / "post.txt").write_text(text, encoding="utf-8")
    (pkg / "README.txt").write_text(INSTRUCTIONS, encoding="utf-8")

    tags = default_tags(session) + ["미국주식뉴스", "해외주식", "뉴스분석"]
    (pkg / "tags.txt").write_text(", ".join(dict.fromkeys(tags)), encoding="utf-8")

    return pkg
