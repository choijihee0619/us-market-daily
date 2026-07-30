"""키 없이 동작하는 룰베이스 폴백.

키워드 매칭이라 정밀도는 LLM보다 낮지만, (1) 비용 0, (2) 완전 결정론적이라
재현 가능, (3) 파이프라인 검증에 충분하다. LLM 결과와 비교하는 baseline으로도 쓴다.
"""
from __future__ import annotations

import re
from typing import Sequence

from .base import LLMProvider

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "통화정책": ["fed", "fomc", "powell", "rate cut", "rate hike", "interest rate",
              "monetary", "central bank", "basis point", "dot plot", "hawkish", "dovish",
              "treasury yield", "quantitative"],
    "인플레이션": ["inflation", "cpi", "ppi", "pce", "price index", "deflation",
               "consumer price", "producer price", "breakeven"],
    "고용": ["jobs", "payroll", "unemployment", "jobless", "labor market", "hiring",
           "layoff", "nonfarm", "wage", "claims"],
    "실적": ["earnings", "revenue", "profit", "eps", "quarterly results", "beat estimates",
           "missed estimates", "margin", "q1", "q2", "q3", "q4 results"],
    "가이던스": ["guidance", "outlook", "forecast", "raises full-year", "cuts full-year",
             "warns", "expects", "projection"],
    "M&A": ["acquisition", "acquires", "merger", "takeover", "buyout", "deal to buy",
            "stake in", "divest", "spin-off", "ipo"],
    "규제정책": ["regulator", "sec ", "ftc", "antitrust", "lawsuit", "probe", "fine",
             "settlement", "tariff", "sanction", "ban", "subpoena", "doj"],
    "지정학": ["war", "conflict", "russia", "ukraine", "china", "taiwan", "middle east",
            "iran", "israel", "election", "trade war", "geopolit"],
    "공급망": ["supply chain", "shortage", "inventory", "logistics", "shipping",
            "production halt", "recall", "factory", "backlog"],
    "AI/데이터센터투자": ["artificial intelligence", " ai ", "data center", "datacenter",
                  "nvidia", "gpu", "chip", "semiconductor", "capex", "cloud",
                  "openai", "llm", "model training"],
    "에너지": ["oil", "crude", "opec", "natural gas", "energy price", "barrel",
            "refinery", "renewable", "solar", "nuclear"],
    "소비": ["consumer", "retail sales", "spending", "holiday sales", "same-store",
           "demand", "traffic", "e-commerce"],
}


class RuleProvider(LLMProvider):
    name = "rule"

    def __init__(self, cfg=None):
        self.cfg = cfg
        self._pats = {
            t: [re.compile(re.escape(k), re.I) for k in kws]
            for t, kws in TOPIC_KEYWORDS.items()
        }

    def classify_topics(self, headlines: Sequence[str], topics: Sequence[str]) -> list[list[str]]:
        out = []
        for h in headlines:
            text = f" {h or ''} "
            hits = [t for t in topics if t in self._pats
                    and any(p.search(text) for p in self._pats[t])]
            out.append(hits)
        return out

    def write_narrative(self, context: dict) -> str:
        """LLM 없이 만드는 서술. 숫자 나열 + 최소한의 연결어만.

        의도적으로 밋밋하게 쓴다. 룰베이스가 화려한 문장을 만들면 근거 없는
        인과 주장이 섞이기 때문이다.
        """
        parts: list[str] = []

        fb = context.get("factors", {})
        if fb:
            ordered = sorted(fb.items(), key=lambda kv: -abs(kv[1]))
            desc = ", ".join(f"{k} {v*100:+.2f}%" for k, v in ordered[:4])
            parts.append(f"팩터 수익률은 {desc} 순으로 절대값이 컸다.")

        sect = context.get("sectors", [])
        if sect:
            top, bot = sect[0], sect[-1]
            parts.append(
                f"섹터는 {top['name']}({top['ret']*100:+.2f}%)가 최상위, "
                f"{bot['name']}({bot['ret']*100:+.2f}%)가 최하위였다."
            )

        mac = context.get("macro", {})
        d10 = mac.get("DGS10")
        if d10 and d10.get("change") is not None:
            # FRED 공개 지연으로 세션일 값이 없을 수 있다. 그 경우 기준일을 명시한다.
            # "마감했다"는 세션일 사실을 주장하는 표현이라 stale일 때는 쓰지 않는다.
            if d10.get("stale"):
                import pandas as _pd
                asof = f"{_pd.Timestamp(d10['asof']):%m-%d}"
                parts.append(f"10년물은 {asof} 기준 {d10['level']:.2f}%이며 "
                             f"직전 공개일 대비 {d10['change']:+.0f}bp다 "
                             f"(해당 거래일 값은 FRED 미공개).")
            else:
                parts.append(f"10년물은 {d10['change']:+.0f}bp 변화해 "
                             f"{d10['level']:.2f}%로 마감했다.")

        tr = context.get("topic_regression", {})
        if tr.get("r2") is not None:
            parts.append(
                f"뉴스 토픽 노출로 설명된 잔차 횡단면 분산은 {tr['r2']*100:.1f}%다 "
                f"(Ridge, n={tr['n']}, in-sample)."
            )
            top3 = list(tr.get("coef", {}).items())[:3]
            if top3:
                s = ", ".join(f"{k} {v:+.0f}bp" for k, v in top3)
                parts.append(f"계수 절대값 상위 토픽은 {s}. 유의성 검정은 하지 않았다.")

        cs = context.get("cross_section", {})
        if cs.get("n"):
            parts.append(
                f"±2σ를 벗어난 종목은 상방 {cs['n_up_outlier']}개, "
                f"하방 {cs['n_down_outlier']}개로 전체 {cs['n']}개 중 "
                f"{(cs['n_up_outlier']+cs['n_down_outlier'])/cs['n']*100:.1f}%다."
            )

        parts.append("이 문단은 규칙 기반으로 생성되었으며 인과관계를 주장하지 않는다.")
        return " ".join(parts)
