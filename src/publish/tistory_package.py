"""티스토리 발행 패키지 (본진 · 애드센스).

티스토리 Open API는 2024년 2월에 완전 종료되었다(파일첨부 → 글 → 댓글 순으로 순차
종료). 따라서 자동 게시 경로는 없고 HTML 붙여넣기만 가능하다.

애드센스를 염두에 둔 구조적 선택:
- 상단 요약 박스: 첫 화면 이탈률을 낮춘다. 광고 노출은 체류시간에 비례한다.
- 목차(앵커): 스크롤 깊이를 늘린다.
- 블록 사이 여백과 구분선: 자동광고가 문단 사이에 삽입될 자리를 만든다.
- 인라인 스타일: 스킨 CSS와 충돌하지 않게 한다.

주의: 애드센스 자동광고를 켜면 구글이 알아서 위치를 잡으므로 수동 광고 코드를
본문에 심을 필요가 없다. 오히려 수동 삽입이 과하면 정책 위반(콘텐츠 대비 광고 과다)
소지가 있다.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

from .common import default_tags, headline_bullets, slug

INSTRUCTIONS = """티스토리 게시 절차 (약 3분)

1. title.txt 제목을 복사해 붙여넣는다.
2. 에디터 우측 상단 [기본모드] -> [HTML] 로 전환한다.
3. post.html 전체를 복사해 붙여넣는다.
4. [기본모드]로 돌아와, images/ 파일을 '[[IMG:파일명]]' 표시 위치에 순서대로
   드래그해 넣고 그 표시 줄은 지운다.
5. 우측 설정에서: 카테고리 지정 / tags.txt 태그 입력 / 공개 / 예약 07:00.
6. 발행.

체크리스트
- [ ] 2번 블록(귀인 서술) 한두 문장을 직접 손봤는가
      -> 매일 실제로 다르게 나오는 유일한 부분이다. 이 한 단계가 자동 생성 티를 지운다.
- [ ] 대표 이미지(썸네일)를 04_residuals.png 또는 01_sectors.png로 지정했는가
- [ ] 자체 도메인이 연결되어 있는가
      -> 티스토리 정책 변경·서비스 리스크에 대비한 유일한 보험이다.
         도메인 없이 쌓은 SEO는 플랫폼에 묶인다.

애드센스 관련
- 자동광고를 켜면 구글이 위치를 잡는다. 본문에 수동 광고 코드를 추가로 심지 말 것.
  콘텐츠 대비 광고 과다는 정책 위반 소지가 있다.
- 금융 카테고리는 E-E-A-T 심사가 빡빡하다. 방법론 페이지와 필자 소개 페이지를
  고정 메뉴에 두면 승인 확률이 올라간다.  [검증 필요]
"""


def _inline(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"_(.+?)_", r'<span style="color:#888;font-size:13px;">\1</span>', s)
    s = re.sub(r"`(.+?)`",
               r'<code style="background:#f4f4f6;padding:1px 5px;border-radius:3px;'
               r'font-size:13px;">\1</code>', s)
    return s


BODY = ("font-family:-apple-system,BlinkMacSystemFont,'Malgun Gothic','Apple SD Gothic Neo',"
        "sans-serif;color:#24292f;font-size:16px;line-height:1.8;")
H2 = ("font-size:21px;font-weight:700;margin:44px 0 14px;padding-bottom:9px;"
      "border-bottom:2px solid #24292f;")
TABLE = "border-collapse:collapse;width:100%;font-size:15px;margin:16px 0;"
TH = ("border:1px solid #d8dade;padding:9px 12px;background:#f6f7f9;"
      "font-weight:600;text-align:left;")
TD = "border:1px solid #d8dade;padding:9px 12px;"


def to_html(markdown_text: str, ctx: dict, repo_url: str | None = None) -> str:
    session = pd.Timestamp(ctx["session"])
    H: list[str] = [f'<div style="{BODY}">']

    # 상단 요약 박스
    bullets = headline_bullets(ctx, 4)
    if bullets:
        H.append('<div style="background:#f6f8fa;border-left:4px solid #24292f;'
                 'padding:16px 20px;margin:0 0 30px;border-radius:0 6px 6px 0;">')
        H.append('<div style="font-weight:700;font-size:14px;color:#57606a;'
                 'margin-bottom:9px;letter-spacing:0.4px;">세 줄 요약</div>')
        for b in bullets:
            H.append(f'<div style="margin:6px 0;font-size:15px;">· {b}</div>')
        H.append("</div>")

    # 목차
    toc = [("m1", "1. 무엇이 움직였나"), ("m2", "2. 무엇이 설명했나"),
           ("m3", "3. 설명되지 않은 움직임"), ("m4", "4. 어제 신호 채점"),
           ("m5", "5. 다음 거래일 일정")]
    H.append('<div style="border:1px solid #e4e6ea;border-radius:6px;'
             'padding:14px 20px;margin:0 0 34px;background:#fff;">')
    H.append('<div style="font-weight:700;font-size:14px;color:#57606a;'
             'margin-bottom:8px;">목차</div>')
    for aid, label in toc:
        H.append(f'<div style="margin:5px 0;font-size:14px;">'
                 f'<a href="#{aid}" style="color:#0969da;text-decoration:none;">{label}</a></div>')
    H.append("</div>")

    # 본문
    heading_idx = 0
    in_table = False
    skip_title = True

    def close_table():
        nonlocal in_table
        if in_table:
            H.append("</tbody></table>")
            in_table = False

    for raw in markdown_text.split("\n"):
        line = raw.rstrip()
        if not line:
            close_table()
            continue

        if line.startswith("# "):
            if skip_title:          # 제목은 티스토리 제목란으로 간다
                skip_title = False
                continue
            continue

        if line.strip() == "---":
            close_table()
            continue                # 섹션 헤더가 이미 구분자 역할을 한다

        if line.startswith("## "):
            close_table()
            aid = toc[heading_idx][0] if heading_idx < len(toc) else f"m{heading_idx+1}"
            heading_idx += 1
            H.append(f'<h2 id="{aid}" style="{H2}">{_inline(line[3:].strip())}</h2>')
            continue

        if line.startswith("!["):
            close_table()
            m = re.search(r"\((.*?)\)", line)
            if m:
                fn = Path(m.group(1)).name
                H.append(f'<p style="text-align:center;color:#8b949e;font-size:13px;'
                         f'background:#fafbfc;border:1px dashed #d0d7de;padding:14px;'
                         f'margin:20px 0;">[[IMG:{fn}]]</p>')
            continue

        if line.startswith(">"):
            close_table()
            H.append(f'<p style="color:#57606a;font-size:14px;margin:0 0 26px;">'
                     f'{_inline(line.lstrip("> "))}</p>')
            continue

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                H.append(f'<table style="{TABLE}"><tbody>')
                in_table = True
                tag, style = "th", TH
            else:
                tag, style = "td", TD
            H.append("<tr>" + "".join(
                f'<{tag} style="{style}">{_inline(c)}</{tag}>' for c in cells) + "</tr>")
            continue

        if line.startswith("- "):
            close_table()
            H.append(f'<p style="margin:6px 0 6px 14px;">· {_inline(line[2:])}</p>')
            continue

        close_table()
        H.append(f'<p style="margin:14px 0;">{_inline(line)}</p>')

    close_table()

    # 하단: 재현성 + 면책
    H.append('<div style="margin-top:46px;padding-top:20px;border-top:1px solid #e4e6ea;'
             'font-size:13px;color:#6e7781;line-height:1.75;">')
    if repo_url:
        rev = ctx.get("git_rev")
        link = f"{repo_url}/commit/{rev}" if rev else repo_url
        H.append(f'<p style="margin:5px 0;">이 글을 생성한 코드와 원본 데이터: '
                 f'<a href="{link}" style="color:#0969da;">GitHub</a></p>')
    H.append('<p style="margin:5px 0;">본 글은 공개 데이터를 자동 수집·분석한 연구 '
             '기록이며 투자자문이 아닙니다. 특정 종목의 매수·매도를 권유하지 않으며, '
             '필자는 언급된 종목을 보유하고 있을 수 있습니다.</p>')
    H.append(f'<p style="margin:5px 0;">대상 거래일 {session:%Y-%m-%d} (ET)</p>')
    H.append("</div></div>")
    return "\n".join(H)


def write_package(session, title: str, markdown: str, ctx: dict,
                  chart_paths: list[Path], out_root: Path,
                  repo_url: str | None = None) -> Path:
    pkg = Path(out_root) / slug(session) / "tistory"
    (pkg / "images").mkdir(parents=True, exist_ok=True)

    html = to_html(markdown, ctx, repo_url)
    (pkg / "title.txt").write_text(title, encoding="utf-8")
    (pkg / "post.html").write_text(html, encoding="utf-8")
    (pkg / "README.txt").write_text(INSTRUCTIONS, encoding="utf-8")
    (pkg / "tags.txt").write_text(", ".join(default_tags(session)), encoding="utf-8")

    for p in chart_paths:
        p = Path(p)
        if p.exists():
            shutil.copy(p, pkg / "images" / p.name)
    return pkg
