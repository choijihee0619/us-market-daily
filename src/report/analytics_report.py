"""블로그 성과 분석.

단순 "조회수 top 10"에 그치지 않는다. 이 프로젝트는 매일 시장 데이터와 자기 신호의
채점 결과를 저장하고 있으므로, **글의 성과를 시장 상황과 교차분석**할 수 있다.
이건 일반 블로그 애널리틱스로는 못 하는 것이다.

던지는 질문:
  Q1. 시장이 크게 움직인 날의 글이 더 읽히는가?
  Q2. 신호가 적중한 날의 글이 더 읽히는가? (독자가 성적표를 보고 오는가)
  Q3. 주간 글이 일간 글보다 오래 읽히는가? (long-tail 검증)
  Q4. RPM이 높은 글의 공통점은 무엇인가?

Q3이 특히 중요하다. "주간 글이 검색 가치가 높다"는 건 지금까지 가설이었고,
이 리포트가 그걸 처음으로 데이터로 확인한다.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

DAILY_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})/?$")
WEEKLY_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})_weekly/?$")


def classify_path(path: str, registry: dict | None = None) -> tuple[str | None, pd.Timestamp | None]:
    """경로에서 글 종류와 대상 날짜를 뽑는다.

    티스토리 포스트 주소가 '숫자'면 경로(`/123`)에 날짜 정보가 없다. 그래서
    scripts/link_post.py 가 만든 매핑(data/post_urls.json)을 먼저 참조한다.
    날짜형 경로(`/2026-07-24`)도 계속 지원하므로 나중에 주소 방식을 바꿔도 된다.
    """
    p = str(path or "").split("?")[0].split("#")[0]
    norm = "/" + p.strip("/") if p.strip("/") else "/"

    if registry:
        hit = registry.get(norm) or registry.get(p) or registry.get(p.rstrip("/"))
        if hit:
            return hit[0], pd.Timestamp(hit[1])

    m = WEEKLY_RE.search(p)
    if m:
        return "weekly", pd.Timestamp(m.group(1))
    m = DAILY_RE.search(p)
    if m:
        return "daily", pd.Timestamp(m.group(1))
    return None, None


def build_post_panel(traffic: pd.DataFrame, earnings: pd.DataFrame,
                     scorecard: pd.DataFrame, prices: pd.DataFrame,
                     registry: dict | None = None) -> pd.DataFrame:
    """글 단위 패널. 성과 지표 + 그 글이 다룬 날의 시장 상황.

    registry: url_registry.path_to_post() 결과. 숫자형 주소를 날짜로 되돌린다.
    """
    if traffic.empty:
        return pd.DataFrame()

    t = traffic.copy()
    t[["kind", "post_date"]] = t["path"].apply(
        lambda p: pd.Series(classify_path(p, registry), index=["kind", "post_date"])
    )
    unresolved = int(t["kind"].isna().sum())
    t = t.dropna(subset=["kind"])
    if t.empty:
        return pd.DataFrame()
    if unresolved:
        # 조용히 버리면 "왜 글이 몇 편밖에 안 잡히지"로 헤매게 된다
        import logging

        logging.getLogger(__name__).info(
            "경로 %d행을 글로 매칭하지 못했다 (about 페이지 등은 정상). "
            "숫자형 주소인데 누락이 많으면 scripts/link_post.py 기록이 빠진 것.",
            unresolved,
        )

    agg = t.groupby(["path", "kind", "post_date"]).agg(
        views=("views", "sum"),
        users=("users", "sum"),
        avg_duration_s=("avg_duration_s", "mean"),
        first_seen=("date", "min"),
        last_seen=("date", "max"),
    ).reset_index()
    agg["days_live"] = (agg["last_seen"] - agg["first_seen"]).dt.days + 1
    agg["views_per_day"] = agg["views"] / agg["days_live"].clip(lower=1)

    if not earnings.empty:
        e = earnings.groupby("path").agg(
            earnings=("earnings", "sum"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
        ).reset_index()
        agg = agg.merge(e, on="path", how="left")
        agg["rpm"] = np.where(agg["impressions"] > 0,
                              agg["earnings"] / agg["impressions"] * 1000, np.nan)
    else:
        for c in ("earnings", "impressions", "clicks", "rpm"):
            agg[c] = np.nan

    # 그 글이 다룬 거래일의 채점 결과
    if scorecard is not None and not scorecard.empty:
        sc = scorecard.copy()
        sc["post_date"] = pd.to_datetime(sc["date"])
        agg = agg.merge(sc[["post_date", "spread_bp", "hit"]], on="post_date", how="left")

    # 그 날의 시장 변동 폭
    if prices is not None and not prices.empty:
        spy = prices[prices["ticker"] == "SPY"][["date", "ret"]].copy()
        spy["post_date"] = pd.to_datetime(spy["date"])
        spy["abs_move"] = spy["ret"].abs()
        agg = agg.merge(spy[["post_date", "ret", "abs_move"]], on="post_date", how="left")

    return agg.sort_values("views", ascending=False).reset_index(drop=True)


def _corr(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    """Spearman 순위상관. 조회수 분포가 심하게 치우쳐 Pearson은 부적절하다."""
    d = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(d) < 8:
        return float("nan"), len(d)
    return float(d["a"].corr(d["b"], method="spearman")), len(d)


def analyze(panel: pd.DataFrame) -> dict:
    if panel.empty:
        return {"available": False}

    out: dict = {"available": True, "n_posts": int(len(panel))}
    out["total_views"] = int(panel["views"].sum())
    out["total_earnings"] = float(panel["earnings"].sum()) if panel["earnings"].notna().any() else None
    out["median_views"] = float(panel["views"].median())

    # Q3. 일간 vs 주간
    kinds = {}
    for kind, g in panel.groupby("kind"):
        kinds[kind] = {
            "n": int(len(g)),
            "views_median": float(g["views"].median()),
            "views_per_day_median": float(g["views_per_day"].median()),
            "duration_median": float(g["avg_duration_s"].median()),
            "earnings": float(g["earnings"].sum()) if g["earnings"].notna().any() else None,
            "rpm_median": float(g["rpm"].median()) if g["rpm"].notna().any() else None,
        }
    out["by_kind"] = kinds
    if "daily" in kinds and "weekly" in kinds and kinds["daily"]["views_per_day_median"] > 0:
        out["weekly_vs_daily_ratio"] = (
            kinds["weekly"]["views_per_day_median"] / kinds["daily"]["views_per_day_median"]
        )

    # Q1. 시장 변동 폭 vs 조회수
    if "abs_move" in panel.columns:
        r, n = _corr(panel["abs_move"], panel["views"])
        out["q1_move_vs_views"] = {"rho": r, "n": n}

    # Q2. 적중 여부 vs 조회수
    if "hit" in panel.columns and panel["hit"].notna().any():
        hit = panel[panel["hit"] == True]["views"]      # noqa: E712
        miss = panel[panel["hit"] == False]["views"]    # noqa: E712
        if len(hit) >= 4 and len(miss) >= 4:
            out["q2_hit_vs_views"] = {
                "hit_median": float(hit.median()),
                "miss_median": float(miss.median()),
                "n_hit": int(len(hit)), "n_miss": int(len(miss)),
            }

    # Q4. RPM 상위 글의 특징
    if panel["rpm"].notna().sum() >= 8:
        hi = panel.nlargest(max(3, len(panel) // 5), "rpm")
        out["q4_high_rpm"] = {
            "rpm_median": float(hi["rpm"].median()),
            "duration_median": float(hi["avg_duration_s"].median()),
            "all_duration_median": float(panel["avg_duration_s"].median()),
            "kinds": hi["kind"].value_counts().to_dict(),
        }

    out["top"] = panel.head(10)[
        [c for c in ("path", "kind", "views", "avg_duration_s", "earnings", "rpm")
         if c in panel.columns]
    ].to_dict("records")
    out["bottom"] = panel.nsmallest(5, "views")[["path", "kind", "views"]].to_dict("records")
    return out


def build_markdown(res: dict, days: int) -> str:
    """이 리포트는 발행용이 아니라 본인이 보는 운영 문서다."""
    L: list[str] = []
    A = L.append
    A(f"# 블로그 성과 리포트 (최근 {days}일)")
    A("")
    if not res.get("available"):
        A("데이터가 없다. GA4/AdSense API를 구성하거나 "
          "`data/analytics/traffic.csv`, `data/analytics/earnings.csv` 를 넣을 것.")
        return "\n".join(L)

    A(f"글 {res['n_posts']}편 · 조회 {res['total_views']:,}회 · "
      f"중앙값 {res['median_views']:.0f}회"
      + (f" · 수익 ${res['total_earnings']:.2f}" if res.get("total_earnings") else ""))
    A("")

    A("## 일간 vs 주간")
    A("")
    A("| 종류 | 편수 | 조회 중앙값 | 일평균 조회 | 체류(초) | RPM |")
    A("|---|---|---|---|---|---|")
    for kind, k in res.get("by_kind", {}).items():
        A(f"| {kind} | {k['n']} | {k['views_median']:.0f} | "
          f"{k['views_per_day_median']:.1f} | {k['duration_median']:.0f} | "
          f"{k['rpm_median']:.2f} |" if k.get("rpm_median") is not None else
          f"| {kind} | {k['n']} | {k['views_median']:.0f} | "
          f"{k['views_per_day_median']:.1f} | {k['duration_median']:.0f} | — |")
    A("")
    ratio = res.get("weekly_vs_daily_ratio")
    if ratio:
        if ratio > 1.3:
            A(f"주간 글의 일평균 조회수가 일간 글의 **{ratio:.1f}배**다. "
              "'주간 글이 검색 가치가 높다'는 가설이 지지된다. "
              "주간 글에 시간을 더 쓰는 게 맞다.")
        elif ratio < 0.8:
            A(f"주간 글의 일평균 조회수가 일간 글의 {ratio:.1f}배에 그친다. "
              "가설과 반대다. 주간 글의 제목·키워드를 재검토할 것.")
        else:
            A(f"주간 대 일간 비율 {ratio:.1f}배로 유의미한 차이가 없다. "
              "표본이 더 쌓여야 판단 가능하다.")
        A("")

    A("## 시장 상황과의 관계")
    A("")
    q1 = res.get("q1_move_vs_views")
    if q1 and not np.isnan(q1["rho"]):
        A(f"**Q1. 시장이 크게 움직인 날의 글이 더 읽히는가?**  "
          f"Spearman ρ = {q1['rho']:+.2f} (n={q1['n']}).")
        if abs(q1["rho"]) < 0.2:
            A("사실상 무관하다. 변동성 큰 날을 노려 홍보하는 전략은 근거가 약하다.")
        elif q1["rho"] > 0:
            A("양의 관계다. 시장이 흔들린 날 유입이 늘어난다. "
              "그런 날은 SNS 공유나 커뮤니티 배포를 병행할 가치가 있다.")
        else:
            A("음의 관계다. 예상과 반대이므로 표본을 더 봐야 한다.")
        A("")

    q2 = res.get("q2_hit_vs_views")
    if q2:
        A(f"**Q2. 신호가 적중한 날의 글이 더 읽히는가?**  "
          f"적중일 중앙값 {q2['hit_median']:.0f}회 (n={q2['n_hit']}), "
          f"불일치일 {q2['miss_median']:.0f}회 (n={q2['n_miss']}).")
        A("독자는 발행 시점에 그날 결과를 모르므로 큰 차이가 없는 게 정상이다. "
          "차이가 크다면 발행 후 재유입(성적표를 보러 오는 독자)이 있다는 뜻이다.")
        A("")

    q4 = res.get("q4_high_rpm")
    if q4:
        A(f"**Q4. RPM 상위 글의 특징.**  체류시간 중앙값 {q4['duration_median']:.0f}초 "
          f"(전체 {q4['all_duration_median']:.0f}초), 구성 {q4['kinds']}.")
        A("")

    A("## 조회 상위 10편")
    A("")
    A("| 경로 | 종류 | 조회 | 체류(초) | 수익 |")
    A("|---|---|---|---|---|")
    for r in res.get("top", []):
        earn = f"${r['earnings']:.2f}" if r.get("earnings") and not pd.isna(r["earnings"]) else "—"
        A(f"| `{r['path']}` | {r['kind']} | {r['views']:,} | "
          f"{r.get('avg_duration_s',0):.0f} | {earn} |")
    A("")

    A("## 하위 5편")
    A("")
    for r in res.get("bottom", []):
        A(f"- `{r['path']}` ({r['kind']}) — {r['views']:,}회")
    A("")
    A("하위 글의 제목을 다시 볼 것. 제목에 숫자가 없거나 검색어와 안 맞을 가능성이 높다.")
    A("")
    A("---")
    A("")
    A("_이 리포트는 발행용이 아니라 운영 문서다. 블로그에 올리지 말 것._")
    return "\n".join(L)
