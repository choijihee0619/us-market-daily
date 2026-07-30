"""키 없이 동작하는 룰베이스 폴백.

키워드 매칭이라 정밀도는 LLM보다 낮지만, (1) 비용 0, (2) 완전 결정론적이라
재현 가능, (3) 파이프라인 검증에 충분하다. LLM 결과와 비교하는 baseline으로도 쓴다.
"""
from __future__ import annotations

import re
from typing import Sequence

from .base import LLMProvider

# 팩터 원시 키(mkt_rf, umd)를 그대로 내보내면 독자가 읽을 수 없다.
FACTOR_KO = {
    "mkt_rf": "시장(MKT)", "smb": "규모(SMB)", "hml": "가치(HML)",
    "rmw": "수익성(RMW)", "cma": "투자(CMA)", "umd": "모멘텀(UMD)", "rf": "무위험",
}

# 그날 최대 변동 팩터를 한 줄로 풀어 쓰기 위한 설명. 팩터의 '롱숏 방향'을 밝혀야
# 부호를 해석할 수 있다. 예를 들어 모멘텀이 음(-)이면 최근 오르던 종목이 오히려
# 더 빠졌다는 뜻이고, 이건 추세 되돌림(reversal)으로 읽힌다.
FACTOR_MEANING = {
    "mkt_rf": "시장 전체 초과수익이다. 이 값이 크면 그날 등락의 대부분은 개별 종목 "
              "이슈가 아니라 시장 방향 하나로 설명된다.",
    "smb": "소형주에서 대형주를 뺀 수익이다. 양(+)이면 소형주가 우위, 음(-)이면 "
           "대형주로 자금이 몰린 날이다.",
    "hml": "저평가(가치)주에서 고평가(성장)주를 뺀 수익이다. 음(-)이면 성장주가 "
           "우위였다는 뜻이다. Fama-French(2015)는 미국 데이터에서 이 팩터가 상당 부분 "
           "다른 팩터에 흡수된다고 보고했으므로 해석을 보수적으로 한다.",
    "rmw": "영업수익성이 높은 기업에서 낮은 기업을 뺀 수익이다. 양(+)이면 質(quality) "
           "선호가 강했던 날이다.",
    "cma": "투자를 적게 하는 기업에서 많이 하는 기업을 뺀 수익이다. 양(+)이면 "
           "보수적 자본배분 기업이 우위였다.",
    "umd": "최근 12개월 상승률 상위 종목에서 하위 종목을 뺀 수익이다(Carhart 1997). "
           "양(+)이면 오르던 게 더 올랐고, 음(-)이면 추세가 되돌려진 날이다. "
           "FF5에는 이 팩터가 없어서 따로 넣는다 — 빼면 모멘텀 노출이 잔차에 남아 "
           "뉴스 효과로 오인된다.",
}

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
            desc = ", ".join(f"{FACTOR_KO.get(k, k)} {v*100:+.2f}%" for k, v in ordered[:4])
            parts.append(f"팩터 수익률은 {desc} 순으로 절대값이 컸다.")
            # 그날 가장 크게 움직인 팩터가 무엇을 뜻하는지 한 줄로 풀어준다.
            # mkt_rf 같은 원시 키를 그대로 내보내면 독자가 읽을 수 없다.
            k0, v0 = ordered[0]
            # 모멘텀은 builder가 부호까지 읽어주는 설명을 따로 붙인다. 중복 방지.
            if k0 in FACTOR_MEANING and k0 != "umd":
                parts.append(f"{FACTOR_KO.get(k0, k0)} 팩터가 가장 크게 움직였다 — "
                             f"{FACTOR_MEANING[k0]}")

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

    def write_news_insight(self, context: dict) -> str:
        """LLM 없이 만드는 네이버용 정리. 사실 나열만 한다."""
        parts: list[str] = []
        dig = context.get("topic_digest") or []
        if dig:
            top = dig[0]
            total = sum(d["n"] for d in dig)
            parts.append(
                f"이날 수집한 기사 {total}건 중 가장 많았던 주제는 "
                f"{top['topic']}({top['n']}건)였습니다."
            )
            if len(dig) > 1:
                parts.append(f"그 다음은 {dig[1]['topic']}({dig[1]['n']}건)였습니다.")
        n_m, n_u = len(context.get("matched") or []), len(context.get("unmatched") or [])
        if n_m or n_u:
            parts.append(
                f"크게 움직인 종목 {n_m + n_u}개 중 {n_m}개는 관련 보도를 확인했고 "
                f"{n_u}개는 수집 범위 안에서 찾지 못했습니다."
            )
            if n_u:
                parts.append("찾지 못했다는 것이 뉴스가 없었다는 뜻은 아닙니다.")
        return " ".join(parts)
