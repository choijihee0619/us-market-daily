"""발행 URL 레지스트리.

왜 필요한가:
티스토리 포스트 주소를 '숫자'로 두면 URL(`/123`)이 **발행하는 순간** 결정된다.
발행 전에는 알 수 없다. 그래서 두 가지가 깨진다.
  1) 네이버 요약본의 원문 링크 -- 어디로 보낼지 모른다
  2) 애널리틱스 -- `/123`에서 날짜를 못 뽑아 성과 분석이 안 된다

'문자' 주소로 바꾸는 대안이 있으나 제목이 한글이라 URL이 퍼센트 인코딩 범벅이 된다.
그래서 숫자를 유지하고, **발행 후 URL을 한 번 기록하는 단계**를 둔다.

이 레지스트리가 (date, kind) <-> url 매핑을 보관하고, 기록 시점에
  - GitHub 포스팅 front matter의 canonical_url
  - 네이버 패키지의 원문 링크
를 소급 갱신한다. 추가 소요는 15초다.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

REGISTRY = "data/post_urls.json"


def _path(repo_root: Path) -> Path:
    return Path(repo_root) / REGISTRY


def load(repo_root: Path) -> dict[str, dict]:
    """{'2026-07-28|daily': {'url':..., 'path':..., 'date':..., 'kind':...}}"""
    p = _path(repo_root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("레지스트리 읽기 실패: %s", e)
        return {}


def key(date, kind: str = "daily") -> str:
    return f"{pd.Timestamp(date):%Y-%m-%d}|{kind}"


def url_path(url: str) -> str:
    """https://host/123?x=1 -> /123"""
    m = re.match(r"https?://[^/]+(/[^?#]*)", str(url).strip())
    return (m.group(1).rstrip("/") or "/") if m else str(url).strip()


def record(repo_root: Path, date, url: str, kind: str = "daily") -> dict:
    reg = load(repo_root)
    k = key(date, kind)
    entry = {
        "date": f"{pd.Timestamp(date):%Y-%m-%d}",
        "kind": kind,
        "url": url.strip(),
        "path": url_path(url),
        "recorded_at": pd.Timestamp.utcnow().tz_localize(None).isoformat(timespec="seconds"),
    }
    reg[k] = entry
    p = _path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=1, sort_keys=True),
                 encoding="utf-8")
    return entry


def get(repo_root: Path, date, kind: str = "daily") -> dict | None:
    return load(repo_root).get(key(date, kind))


def path_to_post(repo_root: Path) -> dict[str, tuple[str, pd.Timestamp]]:
    """URL 경로 -> (kind, date) 역인덱스. 애널리틱스가 쓴다."""
    out: dict[str, tuple[str, pd.Timestamp]] = {}
    for e in load(repo_root).values():
        pth = e.get("path")
        if pth:
            out[pth] = (e.get("kind", "daily"), pd.Timestamp(e["date"]))
    return out


# ------------------------------------------------- 소급 갱신

_FM_CANON = re.compile(r"^canonical_url:.*$", re.M)


def patch_front_matter(repo_root: Path, date, url: str, kind: str = "daily") -> Path | None:
    """posts/*.md 의 canonical_url 을 실제 URL로 교체."""
    d = f"{pd.Timestamp(date):%Y-%m-%d}"
    name = f"{d}.md" if kind == "daily" else f"{d}_weekly.md"
    p = Path(repo_root) / "posts" / name
    if not p.exists():
        log.warning("포스팅 파일 없음: %s", p)
        return None

    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    head, sep, body = text[3:].partition("---")

    if _FM_CANON.search(head):
        head = _FM_CANON.sub(f"canonical_url: {url}", head, count=1)
    else:
        head = head.rstrip("\n") + f"\ncanonical_url: {url}\n"

    p.write_text("---" + head + sep + body, encoding="utf-8")
    return p


_LINK = re.compile(r"^(전체 리포트 →)\s*\S+\s*$", re.M)


def patch_naver(out_root: Path, date, url: str, kind: str = "daily") -> Path | None:
    """네이버 요약본의 원문 링크를 실제 URL로 교체.

    링크 줄이 없으면(도메인 미설정 상태로 생성된 경우) 하단에 추가한다.
    """
    d = f"{pd.Timestamp(date):%Y-%m-%d}"
    folder = d if kind == "daily" else f"{d}_weekly"
    p = Path(out_root) / folder / "naver" / "post.txt"
    if not p.exists():
        log.warning("네이버 요약본 없음: %s", p)
        return None

    text = p.read_text(encoding="utf-8")
    if _LINK.search(text):
        text = _LINK.sub(rf"\1 {url}", text, count=1)
    else:
        lines = text.rstrip("\n").split("\n")
        # 면책 문구 앞에 끼워 넣는다
        idx = next((i for i, l in enumerate(lines) if "투자자문이 아닙니다" in l), len(lines))
        lines.insert(idx, f"전체 리포트 → {url}")
        lines.insert(idx + 1, "")
        text = "\n".join(lines) + "\n"

    p.write_text(text, encoding="utf-8")
    return p
