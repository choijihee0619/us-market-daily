"""5블록 일간 리포트 빌더.

블록 구성:
  1 팩트   - 숫자만. 형용사 없음.
  2 귀인   - 토픽 회귀 + 서술. 인과 주장 금지.
  3 이례치 - ±2σ 이탈 종목과 매칭 뉴스. 설명 안 되면 '미설명'으로 남긴다.
  4 검증   - 어제 신호의 오늘 실현 스프레드. 이 블록이 이 블로그의 존재 이유다.
  5 일정   - 내일 지표·실적.

금지어 목록을 두는 이유: '혼조세', '관망세' 같은 표현은 정보량이 0인데
분량만 채운다. 그런 문장이 쌓이면 양산형 콘텐츠와 구분되지 않는다.
"""
from __future__ import annotations

import re

import pandas as pd

BANNED = ["혼조세", "관망세", "눈치보기", "훈풍", "온기", "숨고르기", "기대감이 커지",
          "주목된다", "전망된다", "귀추가 주목", "투자자들은 촉각"]

SECTION = "\n\n---\n\n"


def _pct(x: float | None, digits: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{x*100:+.{digits}f}%"


def _bp(x: float | None, digits: int = 0) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:+.{digits}f}bp"


def check_banned(text: str) -> list[str]:
    return [w for w in BANNED if w in text]


def make_title(ctx: dict) -> str:
    """날짜 + 숫자 2개 + 반증 가능한 관찰 하나."""
    session = pd.Timestamp(ctx["session"])
    bits: list[str] = []

    mac = ctx.get("macro", {})
    if "DGS10" in mac:
        bits.append(f"10Y {mac['DGS10']['change']:+.0f}bp")
    spy = ctx.get("benchmarks", {}).get("SPY")
    if spy is not None:
        bits.append(f"SPY {spy*100:+.2f}%")

    tr = ctx.get("topic_regression", {})
    if tr.get("r2") is not None:
        bits.append(f"토픽 설명력 {tr['r2']*100:.0f}%")

    cs = ctx.get("cross_section", {})
    tail = ""
    if cs.get("n"):
        n_out = cs["n_up_outlier"] + cs["n_down_outlier"]
        tail = f" — 이례치 {n_out}종목"

    return f"{session:%Y-%m-%d} | " + ", ".join(bits[:3]) + tail


def build_markdown(ctx: dict, narrative: str, chart_files: list[str], cfg) -> str:
    session = pd.Timestamp(ctx["session"])
    L: list[str] = []
    A = L.append

    A(f"# {make_title(ctx)}")
    A("")
    A(f"> 대상 거래일 {session:%Y년 %m월 %d일} (ET) · "
      f"발행 {pd.Timestamp(ctx.get('published_kst', pd.Timestamp.now())):%Y-%m-%d %H:%M} KST")

    # --- 1. 팩트 ---
    A(SECTION)
    A("## 1. 무엇이 움직였나")
    A("")
    bm = ctx.get("benchmarks", {})
    if bm:
        A("| 지수 | 일간 |")
        A("|---|---|")
        for t, label in [("SPY", "S&P 500"), ("QQQ", "나스닥 100"),
                         ("IWM", "러셀 2000"), ("DIA", "다우 30")]:
            if t in bm:
                A(f"| {label} ({t}) | {_pct(bm[t])} |")
        A("")

    sect = ctx.get("sectors", [])
    if sect:
        up = [s for s in sect if s["ret"] > 0]
        A(f"11개 섹터 중 {len(up)}개 상승. "
          f"최상위 {sect[0]['name']} {_pct(sect[0]['ret'])}, "
          f"최하위 {sect[-1]['name']} {_pct(sect[-1]['ret'])}. "
          f"상하단 폭 {(sect[0]['ret']-sect[-1]['ret'])*100:.2f}%p.")
        A("")

    mac = ctx.get("macro", {})
    if mac:
        LBL = {"DGS2": "2년물", "DGS10": "10년물", "T10Y2Y": "10Y-2Y",
               "T10YIE": "기대인플레(10Y BEI)", "DTWEXBGS": "달러지수",
               "VIXCLS": "VIX", "BAMLH0A0HYM2": "하이일드 OAS", "DFF": "EFFR"}
        A("| 신호 | 레벨 | 전일대비 |")
        A("|---|---|---|")
        for k, label in LBL.items():
            if k in mac:
                m = mac[k]
                chg = _bp(m["change"]) if m["unit"] == "bp" else f"{m['change']:+.2f}"
                A(f"| {label} | {m['level']:.2f} | {chg} |")
        A("")

    if chart_files:
        for f in chart_files[:2]:
            A(f"![]({f})")
        A("")

    # --- 2. 귀인 ---
    A(SECTION)
    A("## 2. 무엇이 설명했나")
    A("")
    A(narrative.strip())
    A("")

    tr = ctx.get("topic_regression", {})
    if tr.get("coef"):
        A("잔차 횡단면에 대한 토픽 노출 Ridge 회귀 계수 (bp, in-sample):")
        A("")
        A("| 토픽 | 계수 |")
        A("|---|---|")
        for k, v in list(tr["coef"].items())[:8]:
            A(f"| {k} | {v:+.1f} |")
        A("")
        A(f"n={tr['n']}, R²={tr['r2']*100:.1f}%. "
          "표준오차는 보고하지 않는다. 잔차가 추정된 베타에서 나온 generated regressand라 "
          "2단계 추정오차가 반영되지 않았기 때문이다. 계수는 방향성 참고용이다.")
        A("")

    if len(chart_files) > 2:
        A(f"![]({chart_files[2]})")
        A("")

    # --- 3. 이례치 ---
    A(SECTION)
    A("## 3. 설명되지 않은 움직임")
    A("")
    cs = ctx.get("cross_section", {})
    if cs.get("n"):
        A(f"위험모형: {ctx.get('spec','ff5_umd')} · 추정창 {ctx.get('beta_window',250)}거래일 · "
          f"유효 종목 {cs['n']}개. 잔차 표준편차 {cs['dispersion_bp']:.0f}bp.")
        A("")
        A(f"±2σ 이탈: 상방 {cs['n_up_outlier']}개, 하방 {cs['n_down_outlier']}개.")
        A("")
        A("| 종목 | 수익률 | 잔차 | z | 매칭 뉴스 |")
        A("|---|---|---|---|---|")
        news_map = ctx.get("outlier_news", {})
        for r in (cs.get("top", [])[:5] + cs.get("bottom", [])[:5]):
            hl = news_map.get(r["ticker"])
            hl = (hl[:60] + "…") if hl and len(hl) > 60 else (hl or "**미설명**")
            A(f"| {r['ticker']} | {_pct(r['ret'])} | {_pct(r['residual'])} | "
              f"{r['z']:+.1f} | {hl} |")
        A("")
        unexplained = sum(1 for r in (cs.get("top", []) + cs.get("bottom", []))
                          if r["ticker"] not in news_map)
        A(f"이례치 상하위 20종목 중 {unexplained}개는 수집 범위 안에서 매칭되는 뉴스를 "
          "찾지 못했다. 무료 RSS 커버리지 한계이거나, 실제로 뉴스가 아닌 요인일 수 있다.")
        A("")
    else:
        A("잔차 계산에 필요한 최소 관측치를 확보하지 못했다.")
        A("")

    if len(chart_files) > 3:
        A(f"![]({chart_files[3]})")
        A("")

    # --- 4. 검증 ---
    A(SECTION)
    A("## 4. 어제 신호 채점")
    A("")
    sc = ctx.get("scorecard", {})
    if sc.get("available"):
        A(f"직전 거래일 뉴스 감성(novelty 가중) 5분위 포트폴리오의 당일 실현 잔차:")
        A("")
        A(f"- 최상위 분위 (n={sc['n_top']}): {sc['top_resid_bp']:+.1f}bp")
        A(f"- 최하위 분위 (n={sc['n_bottom']}): {sc['bottom_resid_bp']:+.1f}bp")
        A(f"- 스프레드: **{sc['spread_bp']:+.1f}bp** ({'방향 일치' if sc['hit'] else '방향 불일치'})")
        A("")
        cum = ctx.get("scorecard_cum", {})
        if cum.get("n_days", 0) >= 5:
            A(f"누적 {cum['n_days']}거래일: 평균 스프레드 {cum['mean_bp']:+.1f}bp, "
              f"방향 적중률 {cum['hit_rate']*100:.0f}%, t={cum.get('t_stat', float('nan')):.2f}.")
            A("")
        A("동일가중이며 거래비용을 반영하지 않았다. 집행 가능한 전략 수익률이 아니라 "
          "신호의 방향성 기록이다.")
    else:
        A("채점에 필요한 직전 거래일 신호가 부족하다. 누적 데이터가 쌓이면 자동으로 채워진다.")
    A("")

    # --- 5. 일정 ---
    A(SECTION)
    A("## 5. 다음 거래일 일정")
    A("")
    ev = ctx.get("upcoming", [])
    if ev:
        A("| 시각(ET) | 이벤트 | 컨센서스 |")
        A("|---|---|---|")
        for e in ev[:10]:
            A(f"| {e.get('time','—')} | {e.get('name','')} | {e.get('consensus','—')} |")
    else:
        A("등록된 일정 없음.")
    A("")

    # --- 푸터 ---
    A(SECTION)
    A(f"_{cfg.get_path('disclaimer', '').strip()}_")
    A("")
    src = ctx.get("sources", [])
    if src:
        A("데이터 출처: " + " · ".join(src))
    rev = ctx.get("git_rev")
    if rev:
        A("")
        A(f"생성 커밋: `{rev}`")
    return "\n".join(L)


def to_naver_html(markdown_text: str, image_paths: list[str]) -> str:
    """네이버 스마트에디터 붙여넣기용 HTML.

    네이버 에디터는 마크다운을 해석하지 않고, 붙여넣은 HTML도 상당 부분 정규화한다.
    그래서 표·문단·강조 정도의 최소 태그만 쓰고 CSS는 인라인으로 넣는다.
    이미지는 붙여넣기로 안 넘어가므로 파일을 따로 첨부해야 한다.
    """
    html: list[str] = []
    in_table = False

    def close_table():
        nonlocal in_table
        if in_table:
            html.append("</tbody></table>")
            in_table = False

    for raw in markdown_text.split("\n"):
        line = raw.rstrip()
        if not line:
            close_table()
            continue
        if line.strip() == "---":
            close_table()
            html.append('<hr style="border:none;border-top:1px solid #ddd;margin:24px 0;">')
            continue
        if line.startswith("!["):
            close_table()
            m = re.search(r"\((.*?)\)", line)
            if m:
                html.append(f'<p style="text-align:center;">[이미지 첨부: {m.group(1)}]</p>')
            continue
        if line.startswith("#"):
            close_table()
            lvl = len(line) - len(line.lstrip("#"))
            size = {1: 22, 2: 18, 3: 16}.get(lvl, 15)
            txt = _inline(line.lstrip("# ").strip())
            html.append(f'<p style="font-size:{size}px;font-weight:700;'
                        f'margin:22px 0 10px;">{txt}</p>')
            continue
        if line.startswith(">"):
            close_table()
            html.append(f'<p style="color:#666;font-size:13px;border-left:3px solid #ddd;'
                        f'padding-left:10px;">{_inline(line.lstrip("> "))}</p>')
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                html.append('<table style="border-collapse:collapse;width:100%;'
                            'font-size:14px;margin:12px 0;"><tbody>')
                in_table = True
                tag, style = "th", ("border:1px solid #e0e0e0;padding:7px 10px;"
                                    "background:#f7f7f8;font-weight:600;text-align:left;")
            else:
                tag, style = "td", "border:1px solid #e0e0e0;padding:7px 10px;"
            row = "".join(f'<{tag} style="{style}">{_inline(c)}</{tag}>' for c in cells)
            html.append(f"<tr>{row}</tr>")
            continue
        if line.startswith("- "):
            close_table()
            html.append(f'<p style="margin:4px 0 4px 12px;">· {_inline(line[2:])}</p>')
            continue
        close_table()
        html.append(f'<p style="margin:9px 0;line-height:1.75;">{_inline(line)}</p>')

    close_table()
    return ('<div style="font-family:-apple-system,\'Malgun Gothic\',sans-serif;'
            'color:#222;font-size:15px;">\n' + "\n".join(html) + "\n</div>")


def _inline(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"_(.+?)_", r'<span style="color:#777;font-size:13px;">\1</span>', s)
    s = re.sub(r"`(.+?)`", r'<code style="background:#f2f2f4;padding:1px 4px;'
                           r'border-radius:3px;font-size:13px;">\1</code>', s)
    return s
