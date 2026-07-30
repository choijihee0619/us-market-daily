"""3채널 발행 어댑터 검증.

합성 컨텍스트로 github/tistory/naver 산출물을 만들고 구조를 확인한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402
from src.llm.rule_provider import RuleProvider  # noqa: E402
from src.publish import github_archive as GH  # noqa: E402
from src.publish import naver_package as NAVER  # noqa: E402
from src.publish import tistory_package as TIST  # noqa: E402
from src.report import builder as B  # noqa: E402
from test_pipeline import synth, test_report  # noqa: E402
from src.process import residual as R  # noqa: E402


def make_ctx():
    px, fac, tickers, _ = synth()
    session = pd.Timestamp(fac["date"].iloc[-1])
    resid = R.estimate_residuals(px, fac, session, window=250, min_obs=120)
    cfg = load_config()
    from src.process import attribution as A

    sect_df = A.sector_breakdown(px, session, dict(cfg.get_path("universe.sectors", {})))
    ctx = {
        "session": session,
        "published_kst": pd.Timestamp("2026-07-28 07:00"),
        "spec": "ff5_umd", "beta_window": 250, "git_rev": "a1b2c3d",
        "benchmarks": {"SPY": 0.0043, "QQQ": 0.0071, "IWM": -0.0012, "DIA": 0.0021},
        "sectors": sect_df.to_dict("records"), "sectors_df": sect_df,
        "factors": {"mkt_rf": 0.0043, "smb": -0.0021, "hml": 0.0008,
                    "rmw": 0.0003, "cma": -0.0005, "umd": 0.0034},
        "macro": {"DGS10": {"level": 4.28, "change": -6.0, "unit": "bp"},
                  "DGS2": {"level": 3.91, "change": -3.0, "unit": "bp"},
                  "VIXCLS": {"level": 15.4, "change": -0.8, "unit": "pt"},
                  "BAMLH0A0HYM2": {"level": 3.05, "change": 2.0, "unit": "bp"}},
        "resid_df": resid,
        "cross_section": R.cross_section_stats(resid),
        "topic_regression": {"r2": 0.41, "n": 180,
                             "coef": {"통화정책": 23.4, "실적": -18.1, "AI/데이터센터투자": 12.7}},
        "outlier_news": {},
        "scorecard": {"available": True, "n": 180, "n_top": 36, "n_bottom": 36,
                      "top_resid_bp": 18.2, "bottom_resid_bp": -9.4,
                      "spread_bp": 27.6, "hit": True},
        "scorecard_cum": {"n_days": 22, "mean_bp": 6.4, "hit_rate": 0.59, "t_stat": 1.83},
        "upcoming": [{"time": "08:30", "name": "6월 PCE 물가", "consensus": "+0.2% m/m"}],
        "sources": ["Yahoo Finance", "FRED", "Ken French Data Library"],
    }
    return cfg, ctx, resid


def main():
    cfg, ctx, resid = make_ctx()
    session = ctx["session"]
    title = B.make_title(ctx)
    narrative = RuleProvider(cfg).write_narrative(ctx)
    charts = [Path(p) for p in
              sorted((Path(__file__).resolve().parents[1] / "out" / "_test" / "images").glob("*.png"))]
    md = B.build_markdown(ctx, narrative, [f"images/{p.name}" for p in charts], cfg)

    root = Path(__file__).resolve().parents[1]
    outdir = root / "out" / "_verify"
    canonical = "https://example-domain.kr/2026-07-24"
    repo = "https://github.com/heeppiness/us-market-daily"

    print("[1] GitHub 아카이브")
    tmp = outdir / "repo"
    p = GH.write_post(session, title, md, ctx, charts, tmp, canonical)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "front matter 누락"
    fm = text.split("---")[1]
    for key in ("title:", "date:", "canonical_url:", "spread_bp:", "topic_r2:", "rev:"):
        assert key in fm, f"front matter 키 누락: {key}"
    assert "](images/" not in text, "이미지 경로가 레포 기준으로 안 바뀜"
    assert f"/assets/{pd.Timestamp(session):%Y-%m-%d}/" in text
    sj = GH.append_scorecard_json(ctx, tmp)
    assert sj and sj.exists()
    print(f"  {p.relative_to(tmp)} ({len(text)}자), scorecard.json OK")
    print(f"  front matter: {[l for l in fm.strip().split(chr(10))][:4]}")

    print("\n[2] 티스토리 패키지")
    tp = TIST.write_package(session, title, md, ctx, charts, outdir, repo)
    html = (tp / "post.html").read_text(encoding="utf-8")
    assert html.count("<table") == html.count("</table>"), "표 태그 불균형"
    assert html.count("<div") == html.count("</div>"), "div 태그 불균형"
    assert html.count("<h2") == html.count("</h2>"), "h2 태그 불균형"
    assert "세 줄 요약" in html and "목차" in html
    n_anchor = html.count('id="m')
    assert n_anchor == 5, f"목차 앵커 개수 이상: {n_anchor}"
    for aid in re.findall(r'href="#(m\d)"', html):
        assert f'id="{aid}"' in html, f"앵커 {aid} 대상 없음"
    # repo_url이 GitHub 형식이면 이미지가 raw URL로 인라인된다(붙여넣기 1회).
    assert 'raw.githubusercontent.com' in html and '<img ' in html, \
        "이미지가 인라인되지 않음 -- 붙여넣기 후 드래그가 다시 필요해진다"
    assert "[[IMG:" not in html, "인라인 모드인데 자리표시자가 남음"
    # 레포가 없으면(초기 상태) 자리표시자로 폴백해야 한다
    html_np = TIST.to_html(md, ctx, None, None)
    assert "[[IMG:" in html_np, "폴백 자리표시자 없음"
    assert repo in html, "재현성 링크 없음"
    assert "투자자문이 아닙니다" in html
    assert not re.search(r"^#{1,3} ", html, re.M), "마크다운 헤딩이 그대로 남음"
    print(f"  post.html {len(html)}자, 앵커 5개, 태그 균형 OK")
    print(f"  파일: {sorted(x.name for x in tp.iterdir())}")

    print("\n[3] 네이버 뉴스 리포트")
    news_win = pd.DataFrame({
        "headline": ["Acme beats earnings", "Fed holds rates", "Beta merger deal"],
        "topic": [["실적"], ["통화정책"], ["M&A"]],
        "tickers": [["AAA"], [], ["BBB"]],
        "novelty": [0.9, 0.8, 0.7],
        "sentiment": [0.2, 0.0, 0.1],
    })
    np_ = NAVER.write_package(session, title, ctx, charts, outdir, canonical,
                              news_win=news_win, topics=["실적", "통화정책", "M&A"],
                              insight="오늘은 실적 기사가 가장 많았습니다.",
                              universe={"AAA", "BBB"})
    txt = (np_ / "post.txt").read_text(encoding="utf-8")
    assert txt.count("http") == 1, f"외부 링크가 1개가 아님: {txt.count('http')}"
    assert canonical in txt
    # 티스토리와 축이 달라야 한다: 뉴스가 앞, 계량 용어는 뒤로
    assert "뉴스로 보는 하루" in txt or "뉴스는 어디에" in txt, "뉴스 중심 구성이 아님"
    assert "잔차" not in txt, "네이버 글에 계량 용어가 앞세워짐 (티스토리와 중복)"
    assert "오늘은 실적 기사가 가장 많았습니다" in txt, "인사이트 미반영"
    # 주제별 합계를 전체 건수로 표시하면 안 된다
    assert "여러 주제에 걸치는" in txt, "중복 계산 안내 누락"
    # 제목에는 구분자 '|' 가 쓰이므로, 줄 시작의 마크다운 표만 검사한다
    assert not any(l.lstrip().startswith("|") for l in txt.split("\n")), \
        "마크다운 표가 평문 요약본에 섞임"
    assert "##" not in txt and "**" not in txt, "마크다운 문법이 평문에 남음"
    n_img = len(list((np_ / "images").glob("*.png")))
    assert n_img <= 2, f"이미지 과다: {n_img}"
    ntitle = (np_ / "title.txt").read_text(encoding="utf-8")
    assert ntitle != title, "네이버 제목이 티스토리와 동일 -- 유사문서 위험"
    print(f"  post.txt {len(txt)}자, 제목 분리 확인")
    print(f"  외부 링크 1개, 이미지 {n_img}장")

    print("\n[4] 채널 간 정합성")
    assert canonical in text and canonical in txt, "canonical 불일치"
    assert "example.com" not in html
    print("  canonical 일치, 예시 도메인 잔존 없음")

    print("\n--- 네이버 요약본 미리보기 ---")
    print(txt[:700])
    print("\n전체 통과")


if __name__ == "__main__":
    main()
