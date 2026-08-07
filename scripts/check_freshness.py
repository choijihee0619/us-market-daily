#!/usr/bin/env python3
"""세션 신선도 점검 — 기록이 마지막 거래일보다 뒤처졌는지 확인한다.

매일 5분 붙여넣기 루틴의 **0단계**로 쓴다. 설계는 `docs/SESSION_GAPS.md`
(7장 16번), 이 스크립트는 그 문서의 계층 4다.

왜 필요한가:
스케줄 실행이 빠지면 실패가 아니라 **부재**로 나타난다. 런 자체가 없어서
빨간 X가 뜨지 않고, `last_completed_session()`이 최근 거래일 하나만 돌려주므로
다음날 실행도 그 구멍을 다시 방문하지 않는다. 2026-08-03과 2026-08-06이
그렇게 빠졌고 둘 다 사람이 눈으로 발견했다.

**한계를 분명히 해둔다.** 이 스크립트는 아침 07:00 KST 루틴에서 '어제 세션'의
누락을 잡아주지 못한다. 그 시점은 확정(마감+45분) 후 1시간 남짓이라 정상 실행도
아직 도는 중일 수 있어 PENDING으로 분류된다. 어제 세션이 없다는 건 붙여넣을
`out/{날짜}/` 가 없다는 것으로 루틴 자체가 먼저 알려준다. 이 스크립트가 잡는 건
**그렇게 지나가버린 뒤 남은 오래된 구멍**(2026-08-03 유형)이고, 그걸 사람의 기억이
아니라 종료코드로 만드는 것이 목적이다.

사용:
    python scripts/check_freshness.py                 # 최근 30거래일 점검
    python scripts/check_freshness.py --lookback 90
    python scripts/check_freshness.py --grace-hours 6
    python scripts/check_freshness.py --json

종료코드: MISSING이 하나라도 있으면 1. PENDING만 있으면 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src import freshness as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _archive_gaps(rep: dict) -> list:
    """잔차는 있는데 posts/{날짜}.md 가 없는 세션.

    실패의 성격이 다르므로 종료코드에는 반영하지 않는다. 갭 메움은 블로그
    산출물을 만들지 않도록 설계했으므로(SESSION_GAPS 3절 계층 1) 정상일 수 있다.
    """
    have_posts = {p.stem for p in (ROOT / "posts").glob("*.md")}
    out = []
    for d in rep.get("expected", []):
        if d in rep.get("missing", []) or d in rep.get("pending", []):
            continue
        if str(pd.Timestamp(d).date()) not in have_posts:
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=30,
                    help="점검할 최근 거래일 수 (기본 30)")
    ap.add_argument("--grace-hours", type=float, default=F.DEFAULT_GRACE_HOURS,
                    help="확정 후 이 시간까지는 PENDING으로 본다 (기본 3)")
    ap.add_argument("--table", default=F.PRIMARY_TABLE,
                    help="기준 테이블 (기본 residuals)")
    ap.add_argument("--json", action="store_true", help="기계 판독용 출력")
    args = ap.parse_args()

    rep = F.audit(lookback=args.lookback, grace_hours=args.grace_hours,
                  table=args.table)

    if args.json:
        print(json.dumps({
            "status": rep["status"],
            "last_session": str(rep["last_session"].date()) if rep["last_session"] is not None else None,
            "latest_present": str(rep["latest_present"].date()) if rep["latest_present"] is not None else None,
            "lag_sessions": rep["lag_sessions"],
            "missing": [str(d.date()) for d in rep["missing"]],
            "pending": [str(d.date()) for d in rep["pending"]],
        }, ensure_ascii=False, indent=2))
        return 1 if rep["missing"] else 0

    print(f"세션 신선도 점검 — 최근 {args.lookback}거래일")
    print("-" * 60)
    print(F.format_report(rep, archive_missing=_archive_gaps(rep)))
    print("-" * 60)
    print(f"판정: {rep['status']}")
    return 1 if rep["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
