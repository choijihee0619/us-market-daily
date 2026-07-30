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
    # 세션일 값이 미공개(stale)이거나 변화량이 없으면 제목에 넣지 않는다.
    # 제목은 그 거래일의 사실이어야 하고, 직전 공개일 변화를 세션 변화처럼
    # 쓰면 안 된다. 빼면 SPY 수익률이 앞으로 온다.
    d10 = mac.get("DGS10")
    if d10 and d10.get("change") is not None and not d10.get("stale"):
        bits.append(f"10Y {d10['change']:+.0f}bp")
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
    A("_이 블록은 숫자만 적는다. 해석은 2번으로 넘긴다._")
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
        # FRED 공개 지연이 시리즈마다 달라서 기준일을 함께 싣는다. 기준일을 숨기고
        # 세션일 값처럼 보이게 하면 1번 블록이 팩트가 아니게 된다.
        A("| 신호 | 레벨 | 직전대비 | 기준일 |")
        A("|---|---|---|---|")
        for k, label in LBL.items():
            if k not in mac:
                continue
            m = mac[k]
            if m.get("change") is None:
                chg = "—"
            else:
                chg = _bp(m["change"]) if m["unit"] == "bp" else f"{m['change']:+.2f}"
            asof = m.get("asof")
            asof_s = f"{pd.Timestamp(asof):%m-%d}" if asof is not None else "—"
            if m.get("stale"):
                asof_s += " (미공개)"
            A(f"| {label} | {m['level']:.2f} | {chg} | {asof_s} |")
        A("")
        if any(mac[k].get("stale") for k in LBL if k in mac):
            A("_'미공개'는 해당 거래일 값이 아직 FRED에 공개되지 않아 직전 공개일 "
              "기준임을 뜻한다. 확정치가 들어오면 소급 갱신된다._")
            A("")

    if chart_files:
        for f in chart_files[:2]:
            A(f"![]({f})")
        A("")

    # --- 2. 귀인 ---
    A(SECTION)
    A("## 2. 무엇이 설명했나")
    A("")
    A("_개별 종목의 등락은 대부분 '그 종목 고유의 사건'이 아니라 시장 전체·규모·가치·"
      "모멘텀 같은 **공통 요인(팩터)** 에 대한 노출로 설명된다. 이 블록은 그 공통 요인이 "
      "그날 얼마나 움직였는지를 먼저 본다._")
    A("")
    A(narrative.strip())
    A("")

    # 모멘텀은 매일 등장하고 가장 오해하기 쉬운 팩터다. 부호에 따라 뜻이 정반대이므로
    # 그날 부호를 읽어주는 설명을 자동으로 붙인다. 서술(LLM/룰)에 맡기면 표현이
    # 흔들리고, 정의를 매번 다시 만들어 쓰면 틀릴 여지가 있다.
    umd = (ctx.get("factors") or {}).get("umd")
    if umd is not None:
        v = umd * 100
        if v < -0.15:
            read = ("**음(−)이다. 최근 잘 오르던 종목이 그날 오히려 더 많이 빠졌다는 "
                    "뜻이다(되돌림). 최근 상승세가 강한 종목을 들고 있었다면 개별 "
                    "뉴스가 없어도 손실이 났을 날이다.**")
        elif v > 0.15:
            read = ("**양(+)이다. 오르던 종목이 그날도 더 올랐다는 뜻이다(추세 지속). "
                    "최근 상승 종목에 몰려 있던 자금에 유리했던 날이다.**")
        else:
            read = "0에 가깝다. 최근 상승·하락 종목 사이에 뚜렷한 방향 차이가 없었다."
        A(f"_**모멘텀(UMD) {v:+.2f}%** — 최근 약 1년간 많이 오른 종목을 사고 많이 내린 "
          f"종목을 파는 가상 포트폴리오의 그날 수익률이다. {read} "
          f"이 팩터를 모형에 넣지 않으면 이 움직임이 잔차에 남아 뉴스 효과로 "
          f"오인된다._")
        A("")

    tr = ctx.get("topic_regression", {})
    if tr.get("coef"):
        A("아래는 **팩터로 설명되지 않고 남은 부분(잔차)** 을 뉴스 토픽 노출로 회귀한 "
          "결과다. 계수가 +면 그날 그 토픽에 노출된 종목들의 잔차가 평균적으로 높았다는 "
          "뜻이고, 인과관계가 아니라 같은 날 함께 나타난 연관이다.")
        A("")
        A("| 토픽 | 계수 (bp) |")
        A("|---|---|")
        for k, v in list(tr["coef"].items())[:8]:
            A(f"| {k} | {v:+.1f} |")
        A("")
        A(f"n={tr['n']}, R²={tr['r2']*100:.1f}%. "
          f"R²는 잔차 횡단면 분산 중 토픽이 설명한 비중이다 — 나머지 "
          f"{100-tr['r2']*100:.0f}%는 이 토픽 집합으로 설명되지 않았다. "
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
        A("_**잔차(residual)** 는 '그 종목이 팩터 노출만으로 움직였다면 이랬을 텐데, "
          "실제로는 이만큼 달랐다'의 차이다. 예를 들어 시장이 -1.5% 빠진 날 어떤 종목이 "
          "-1.5% 하락했다면 시장을 따라간 것이라 잔차는 0에 가깝다. 반대로 시장이 빠진 날 "
          "+16% 오르면 팩터로 설명되지 않는 큰 양(+)의 잔차가 남는다. 그 남은 부분이 "
          "그 종목 고유의 사건 — 실적 발표, 가이던스 수정, M&A 같은 — 을 담고 있을 "
          "가능성이 높다. 이 블로그가 뉴스와 맞춰보는 대상이 바로 이 잔차다._")
        A("")
        A(f"위험모형: {ctx.get('spec','ff5_umd')} · 추정창 {ctx.get('beta_window',250)}거래일 · "
          f"유효 종목 {cs['n']}개. 잔차 표준편차 {cs['dispersion_bp']:.0f}bp.")
        A("")
        n_out = cs["n_up_outlier"] + cs["n_down_outlier"]
        share = n_out / cs["n"] * 100 if cs["n"] else 0
        A(f"±2σ 이탈: 상방 {cs['n_up_outlier']}개, 하방 {cs['n_down_outlier']}개 "
          f"(전체 {cs['n']}개 중 {share:.1f}%). **z** 는 그 종목의 잔차를 자기 과거 "
          f"변동성으로 나눈 값이다. z=+3이면 평소 흔들림의 세 배만큼 위로 벗어났다는 뜻이고, "
          f"정규분포라면 ±2σ 밖은 약 4.6%만 나온다 — 그보다 많으면 그날 개별 사건이 "
          f"몰렸거나 수익률 분포의 꼬리가 두껍다는 신호다.")
        A("")
        # 티커만 쓰면 미국 개별종목에 익숙하지 않은 독자에게 정보가 0이다.
        # 회사명·섹터는 구성종목 파일에 이미 있으므로 함께 싣고, 뉴스는 링크를 걸어
        # 독자가 원문을 직접 확인할 수 있게 한다(서술의 검증 가능성).
        A("| 종목 | 회사 | 섹터 | 수익률 | 잔차 | z | 같은 날 보도된 뉴스 |")
        A("|---|---|---|---|---|---|---|")
        news_map = ctx.get("outlier_news", {})
        url_map = ctx.get("outlier_news_url", {})
        src_map = ctx.get("outlier_news_source", {})
        for r in (cs.get("top", [])[:5] + cs.get("bottom", [])[:5]):
            t = r["ticker"]
            hl = news_map.get(t)
            if hl:
                short = (hl[:52] + "…") if len(hl) > 52 else hl
                short = short.replace("|", "·")          # 표 구분자 깨짐 방지
                u = url_map.get(t)
                cell = f"[{short}]({u})" if u else short
                src = src_map.get(t)
                if src:
                    cell += f" _{src}_"
            else:
                cell = "**미설명**"
            A(f"| {t} | {r.get('name', t)} | {r.get('sector', '')} | "
              f"{_pct(r['ret'])} | {_pct(r['residual'])} | {r['z']:+.1f} | {cell} |")
        A("")
        unexplained = sum(1 for r in (cs.get("top", []) + cs.get("bottom", []))
                          if r["ticker"] not in news_map)
        A(f"이례치 상하위 20종목 중 {unexplained}개는 수집 범위 안에서 매칭되는 뉴스를 "
          "찾지 못했다. 뉴스 커버리지 한계이거나, 실제로 뉴스가 아닌 요인일 수 있다. "
          "둘을 구분하지 않고 '뉴스로 설명되지 않는다'고 단정하지 않는다.")
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
    A("_이 블로그의 존재 이유다. 전날 뉴스 감성으로 종목을 5분위로 나눠 놓고, 그 다음 "
      "거래일에 실제로 상위 분위가 하위 분위보다 높은 잔차를 냈는지 채점한다. **맞은 날과 "
      "틀린 날을 모두 그대로 남긴다.** 사후에 성공 사례만 고르면 out-of-sample 기록이 "
      "아니게 되기 때문이다._")
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
        A("_아래는 다음 거래일에 공개되는 주요 지표다. 여기 있는 항목은 **전 종목의 잔차가 "
          "같은 방향으로 움직이기 쉬운 날**을 미리 표시해 둔 것이다. 그런 날은 개별 뉴스 "
          "효과와 매크로 충격이 섞여 3번 블록의 해석이 어려워진다(횡단면 상관이 커진다). "
          "전망이 아니라 해석 난이도에 대한 예고다._")
        A("")
        A("| 발표일 | 지표 | 왜 중요한가 |")
        A("|---|---|---|")
        for e in ev[:10]:
            A(f"| {e.get('date','—')} | {e.get('name','')} | {e.get('why','—')} |")
        A("")
        A("_일정은 FRED 릴리스 캘린더 기준이며 시각은 지표마다 다르다. "
          "컨센서스(시장 예상치)는 무료 소스로 확보되지 않아 싣지 않는다._")
    else:
        A("_다음 거래일에 예정된 주요 지표가 없다(또는 일정 수집에 실패했다)._")
    A("")

    # --- 용어 ---
    # 매일 같은 표가 붙지만, 이 글은 처음 온 독자에게도 자기완결적이어야 한다.
    # 대신 짧게 유지한다(6항목). 자세한 설명은 고정 방법론 페이지로 넘긴다.
    A(SECTION)
    A("## 용어")
    A("")
    A("| 용어 | 뜻 |")
    A("|---|---|")
    A("| 팩터 | 개별 종목 수익률을 공통으로 움직이는 요인. 시장·규모·가치·수익성·투자·모멘텀 6개를 쓴다 |")
    A("| 모멘텀 (UMD) | 최근 오른 종목에서 내린 종목을 뺀 수익. 양(+)이면 추세 지속, 음(-)이면 되돌림 |")
    A("| 잔차 | 팩터 노출로 설명한 뒤 남은 부분. 그 종목 고유의 사건을 담는다 |")
    A("| z | 잔차를 그 종목의 평소 변동성으로 나눈 값. ±2를 넘으면 이례치로 본다 |")
    A("| bp | 베이시스포인트. 1bp = 0.01% |")
    A("| σ (시그마) | 표준편차. 흔들림의 크기 단위 |")
    A("")
    A("_위험모형은 Fama-French 5팩터 + Carhart 모멘텀이다. **수익률을 예측하는 모형이 "
      "아니라 알려진 위험 노출을 걷어내는 귀인 모형**이다. 걷어낸 뒤 남은 잔차가 이 "
      "기록의 분석 대상이다._")
    A("")
    mu = str(cfg.get_path("report.methodology_url", "") or "").strip()
    if mu:
        A(f"용어·모형·한계를 처음부터 정리한 안내 글이 있다 → "
          f"[이 블로그를 읽는 방법]({mu})")
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
