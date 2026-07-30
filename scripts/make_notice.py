#!/usr/bin/env python3
"""고정 페이지(공지) 붙여넣기 패키지 생성.

왜 스크립트로 두는가:
방법론 페이지는 한 번 쓰고 끝나지 않는다. 모형을 바꾸거나 한계 항목이 추가되면
같이 갱신해야 하는데, 티스토리에 붙여넣은 HTML은 원본이 아니다. 원본을
docs/METHODOLOGY.md 로 저장소에 두고 여기서 HTML을 다시 뽑으면, 수정 이력이
git에 남고 코드와 문서가 어긋나지 않는다.

애드센스 관점에서도 필요하다. 금융 카테고리는 E-E-A-T 심사가 빡빡해서 방법론·필자
소개 페이지를 고정 메뉴에 두는 편이 유리하다(CLAUDE.md 7장 9번).  [검증 필요]

사용:
    python scripts/make_notice.py
    python scripts/make_notice.py --source docs/METHODOLOGY.md --slug methodology
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import OUT_DIR, load_config  # noqa: E402
from src.publish import tistory_package as TIST  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

INSTRUCTIONS = """공지 게시 절차 (약 3분)

1. title.txt 제목을 복사해 붙여넣는다.
2. 에디터 우측 상단 [기본모드] -> [HTML] 로 전환한다.
3. post.html 전체를 복사해 붙여넣는다.
4. 우측 설정에서 tags.txt 태그를 입력하고 공개로 발행한다.
5. **관리 -> 메뉴에서 이 글을 고정 메뉴(또는 공지)로 등록한다.**
   -> 이 단계가 핵심이다. 매일 글에서 독자가 찾아올 수 있어야 의미가 있다.
6. 발행 후 URL을 config.yaml 의 report.methodology_url 에 적는다.
   그러면 매일 글 하단 용어 표 아래에 "자세한 설명" 링크가 자동으로 붙는다.

주의
- 이 글은 한 번만 발행한다. 내용을 고칠 때는 docs/METHODOLOGY.md 를 수정하고
  이 스크립트를 다시 돌린 뒤 **기존 글을 수정**한다. 새 글로 올리면 중복 콘텐츠다.
- 이미지가 없는 글이라 붙여넣기만으로 끝난다.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="docs/METHODOLOGY.md")
    ap.add_argument("--slug", default="methodology")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    load_config(args.config)

    src = Path(args.source)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"원본 없음: {src}")
        return 1

    md = src.read_text(encoding="utf-8")

    # 첫 '# ' 줄을 제목으로 쓴다. to_static_html이 본문에서는 뺀다.
    m = re.search(r"^#\s+(.+)$", md, re.M)
    title = m.group(1).strip() if m else "방법론"

    html = TIST.to_static_html(md)

    pkg = OUT_DIR / "_notice" / args.slug
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "title.txt").write_text(title, encoding="utf-8")
    (pkg / "post.html").write_text(html, encoding="utf-8")
    (pkg / "README.txt").write_text(INSTRUCTIONS, encoding="utf-8")
    (pkg / "tags.txt").write_text(
        "방법론, 팩터투자, 파마프렌치, 모멘텀, 퀀트, 잔차분석, 미국주식, 금융공학",
        encoding="utf-8")

    n_head = len(re.findall(r"^##\s", md, re.M))
    print(f"제목: {title}")
    print(f"섹션 {n_head}개 · 본문 {len(md):,}자 -> HTML {len(html):,}자")
    print(f"저장: {pkg}")
    print()
    print((pkg / "README.txt").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
