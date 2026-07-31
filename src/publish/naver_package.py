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

INSTRUCTIONS = """네이버 블로그 게시 절차 — 글 2개 (합계 약 4분)

이 폴더에는 성격이 다른 글 두 개가 있다. 검색 의도가 달라 나눠 둔 것이다.

  1_summary/  "미국증시 마감" 쿼리 대응. 스캔용 숫자
  2_news/     "무슨 일이 있었나" 대응. 읽는 글

각 폴더에서:
1. title.txt 제목을 붙여넣는다.
2. post.txt 내용을 붙여넣는다. 평문이라 그대로 들어간다.
3. 이미지 1~2장 첨부 (선택). 파일은 assets/{날짜}/ 에 있다.
4. tags.txt 태그 입력 -> 공개 -> 예약.

**발행 시각을 벌릴 것**
  티스토리   07:00
  1_summary  07:10   (원문이 먼저 색인되도록 10분 뒤)
  2_news     12:00 이후 또는 저녁

같은 날 두 글을 연달아 올리면 유사문서로 묶이거나 스팸으로 보일 수 있다.
반나절 벌리면 노출 시간대도 두 번 가져간다.

주의
- 티스토리 전문을 복사해 오지 말 것. 원문 순위를 잡아먹는다.
- 외부 링크는 글당 1개만 유지한다.
- 2_news 의 '오늘의 정리'는 직접 고쳐 쓰는 것을 권한다. 매일 같은 골격이
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


def build_summary(ctx: dict, canonical_url: str | None = None) -> str:
    """글 A — 마감 숫자 요약 (평문).

    뉴스 리포트와 목적이 다르다. 이쪽은 **검색 대응**이다. 네이버 유입은
    "미국증시 마감", "뉴욕증시 마감" 같은 쿼리로 들어오고, 그런 독자는 스캔할 숫자를
    원한다. 뉴스 리포트는 앞부분이 서술이라 그 의도에 안 맞는다.

    티스토리 1번 블록과 같은 데이터지만 저쪽은 HTML 표이고 여기는 평문 목록이라
    문장이 겹치지 않는다. 숫자 나열은 어느 매체나 비슷하게 쓰므로 유사문서 판정에서
    문제되는 부분이 아니다.
    """
    session = pd.Timestamp(ctx["session"])
    L: list[str] = []
    A = L.append

    A(f"{session:%Y년 %m월 %d일}(현지시간) 미국 증시 마감 기록입니다.")
    A("전망이나 매매 권유가 아니라 그날의 숫자를 그대로 남깁니다.")
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
        up = [s for s in sect if s["ret"] > 0]
        A("[ 섹터 ]")
        A(f"11개 중 {len(up)}개 상승")
        for s in sect[:3]:
            A(f"  {s['name']}   {s['ret']*100:+.2f}%")
        if len(sect) > 3:
            A("  ...")
            A(f"  {sect[-1]['name']}   {sect[-1]['ret']*100:+.2f}%")
        A("")

    mac = ctx.get("macro", {})
    if mac:
        A("[ 금리·변동성 ]")
        LBL = {"DGS2": "미국채 2년", "DGS10": "미국채 10년",
               "VIXCLS": "VIX", "BAMLH0A0HYM2": "하이일드 스프레드"}
        stale_any = False
        for k, label in LBL.items():
            m = mac.get(k)
            if not m:
                continue
            chg = ("—" if m.get("change") is None else
                   (f"{m['change']:+.0f}bp" if m["unit"] == "bp" else f"{m['change']:+.2f}"))
            mark = ""
            if m.get("stale") and m.get("asof") is not None:
                mark = f"  * {pd.Timestamp(m['asof']):%m-%d} 기준"
                stale_any = True
            A(f"{label}   {m['level']:.2f}   ({chg}){mark}")
        if stale_any:
            # 공개 지연을 숨기면 없는 사실을 그날 수치처럼 싣게 된다.
            A("")
            A("* 표시는 해당 거래일 값이 아직 공개되지 않아 직전 공개일 기준입니다.")
        A("")

    cs = ctx.get("cross_section", {})
    if cs.get("n"):
        n_out = cs.get("n_up_outlier", 0) + cs.get("n_down_outlier", 0)
        A("[ 시장 요인으로 설명되지 않은 움직임 ]")
        A(f"분석 대상 {cs['n']}개 중 {n_out}개 종목이 ±2σ를 벗어났습니다.")
        A("지수·업종 같은 공통 요인을 걷어낸 뒤에도 남은 변동입니다.")
        A("")
        for r in cs.get("top", [])[:3]:
            A(f"  ▲ {r.get('name', r['ticker'])}({r['ticker']})  {r['ret']*100:+.2f}%")
        for r in cs.get("bottom", [])[:3]:
            A(f"  ▼ {r.get('name', r['ticker'])}({r['ticker']})  {r['ret']*100:+.2f}%")
        A("")

    sc = ctx.get("scorecard", {})
    if sc.get("available"):
        A("[ 전날 신호 채점 ]")
        A("전날 뉴스 감성 상위 20% 종목과 하위 20% 종목의")
        A("당일 실현 초과수익 차이입니다.")
        A("")
        A(f"스프레드  {sc['spread_bp']:+.1f}bp  "
          f"({'방향 일치' if sc['hit'] else '방향 불일치'})")
        cum = ctx.get("scorecard_cum", {})
        if cum.get("n_days", 0) >= 5:
            A(f"누적 {cum['n_days']}거래일 방향 적중률  {cum['hit_rate']*100:.0f}%")
        A("")
        A("맞은 날과 틀린 날을 모두 그대로 남깁니다.")
        A("")

    A("─" * 22)
    A("")
    A("종목별 잔차 계산 방식, 뉴스 주제 회귀, 사용한 모형과 코드는")
    A("전체 리포트에 있습니다.")
    if canonical_url:
        A("")
        A(f"전체 리포트 → {canonical_url}")
    A("")
    A("본 글은 공개 데이터를 자동 수집·분석한 연구 기록이며 투자자문이 아닙니다.")
    A("특정 종목의 매수·매도를 권유하지 않습니다.")
    return "\n".join(L)


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
    """네이버용 글 **두 개**를 만든다.

    하나로 합치지 않는 이유: 검색 의도가 다르다.
      1_summary — "미국증시 마감" 쿼리 대응. 스캔용 숫자
      2_news    — "무슨 일이 있었나" 대응. 읽는 글
    한 글에 둘을 다 넣으면 앞부분이 숫자면 뉴스 독자가 이탈하고, 앞부분이 서술이면
    검색 스니펫에 숫자가 안 잡힌다.

    같은 날 두 글을 연달아 올리면 유사문서로 묶일 수 있으므로 시간을 벌린다
    (README 참조).
    """
    session_ts = pd.Timestamp(session)
    base = Path(out_root) / slug(session) / "naver"
    base.mkdir(parents=True, exist_ok=True)
    (base / "README.txt").write_text(INSTRUCTIONS, encoding="utf-8")

    # ------------------------------------------------ 글 A: 숫자 요약
    a = base / "1_summary"
    a.mkdir(exist_ok=True)
    bm = ctx.get("benchmarks", {})
    sect = ctx.get("sectors", [])
    bits = []
    if bm.get("SPY") is not None:
        bits.append(f"S&P500 {bm['SPY']*100:+.2f}%")
    if sect:
        bits.append(f"{sect[0]['name']} {sect[0]['ret']*100:+.2f}%")
    a_title = f"[{session_ts:%m/%d} 미국증시 마감] " + ", ".join(bits[:2])
    (a / "title.txt").write_text(a_title, encoding="utf-8")
    (a / "post.txt").write_text(build_summary(ctx, canonical_url), encoding="utf-8")
    (a / "tags.txt").write_text(
        ", ".join(dict.fromkeys(default_tags(session) +
                                ["미국증시마감", "뉴욕증시", "해외주식", "증시마감"])),
        encoding="utf-8")

    # ------------------------------------------------ 글 B: 뉴스 리포트
    b = base / "2_news"
    b.mkdir(exist_ok=True)
    (b / "title.txt").write_text(
        f"[{session_ts:%m/%d} 미국장] 뉴스로 보는 하루", encoding="utf-8")
    (b / "post.txt").write_text(
        build_report(ctx, title, canonical_url, news_win, topics, insight, universe),
        encoding="utf-8")
    (b / "tags.txt").write_text(
        ", ".join(dict.fromkeys(default_tags(session) +
                                ["미국주식뉴스", "해외주식", "뉴스분석"])),
        encoding="utf-8")
    return base
