"""GitHub 아카이브 (완전 자동).

세 채널 중 유일하게 API 제약이 없다. git push로 끝난다.

역할은 수익원이 아니라 자산이다:
  - 재현성: 각 포스팅에 생성 커밋 해시를 박아 코드-데이터-결론을 연결한다.
  - 데이터셋: 일별 패널이 쌓이면 그 자체가 연구 자산이 된다.
  - 크리덴셜: 공개 트랙레코드는 리서치 직군 포트폴리오다.

canonical URL은 티스토리(수익이 나는 쪽)를 가리킨다. GitHub Pages를 켜더라도
검색 순위를 티스토리가 가져가게 하기 위함이다.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from .common import default_tags, headline_bullets, slug


def _front_matter(session, title: str, ctx: dict, canonical_url: str | None) -> str:
    """Jekyll front matter. 지금 Pages를 안 켜도 나중에 그대로 쓸 수 있다."""
    d = pd.Timestamp(session)
    fm: list[str] = ["---"]
    fm.append(f'title: "{title.replace(chr(34), chr(39))}"')
    fm.append(f"date: {d:%Y-%m-%d}")
    fm.append("layout: post")
    fm.append("categories: [daily]")
    fm.append("tags: [" + ", ".join(default_tags(session)) + "]")
    if canonical_url:
        fm.append(f"canonical_url: {canonical_url}")

    bullets = headline_bullets(ctx, 2)
    if bullets:
        one = " ".join(bullets).replace('"', "'")
        fm.append(f'description: "{one[:180]}"')

    # 사후 집계·검색에 쓰기 좋게 핵심 수치를 구조화해 둔다
    bm = ctx.get("benchmarks", {})
    if bm.get("SPY") is not None:
        fm.append(f"spy_ret: {bm['SPY']:.6f}")
    sc = ctx.get("scorecard", {})
    if sc.get("available"):
        fm.append(f"spread_bp: {sc['spread_bp']:.2f}")
        fm.append(f"hit: {str(sc['hit']).lower()}")
    tr = ctx.get("topic_regression", {})
    if tr.get("r2") is not None:
        fm.append(f"topic_r2: {tr['r2']:.4f}")
    if ctx.get("git_rev"):
        fm.append(f"rev: {ctx['git_rev']}")
    fm.append("---")
    return "\n".join(fm)


def write_post(session, title: str, markdown: str, ctx: dict,
               chart_paths: list[Path], repo_root: Path,
               canonical_url: str | None = None) -> Path:
    """posts/YYYY-MM-DD.md + assets/YYYY-MM-DD/*.png 를 레포에 기록."""
    d = slug(session)
    posts = Path(repo_root) / "posts"
    assets = Path(repo_root) / "assets" / d
    posts.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    for p in [Path(x) for x in chart_paths]:
        if p.exists():
            shutil.copy(p, assets / p.name)

    body = markdown
    # 이미지 경로를 레포 기준으로 바꾼다
    body = body.replace("](images/", f"](/assets/{d}/")

    out = posts / f"{d}.md"
    out.write_text(_front_matter(session, title, ctx, canonical_url) + "\n\n" + body,
                   encoding="utf-8")
    return out


def append_scorecard_json(ctx: dict, repo_root: Path) -> Path | None:
    """누적 성적표를 JSON으로 유지. 나중에 대시보드 페이지에서 바로 읽는다."""
    sc = ctx.get("scorecard", {})
    if not sc.get("available"):
        return None

    p = Path(repo_root) / "data" / "scorecard.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    if p.exists():
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            rows = []

    d = slug(ctx["session"])
    rows = [r for r in rows if r.get("date") != d]
    rows.append({
        "date": d,
        "spread_bp": round(float(sc["spread_bp"]), 2),
        "top_bp": round(float(sc["top_resid_bp"]), 2),
        "bottom_bp": round(float(sc["bottom_resid_bp"]), 2),
        "hit": bool(sc["hit"]),
        "n": int(sc["n"]),
        "rev": ctx.get("git_rev"),
    })
    rows.sort(key=lambda r: r["date"])
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
