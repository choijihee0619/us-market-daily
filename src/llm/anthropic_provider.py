"""Anthropic API 어댑터.

비용 설계: 분류는 저가 모델(Haiku)로 배치 처리하고, 최종 원고 1회만 상위 모델을 쓴다.
헤드라인 120건 분류 ≈ 입력 6~8k 토큰이므로 하루 비용은 원고 생성 쪽이 지배한다.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence

from ..config import env
from .base import LLMProvider

log = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """당신은 금융 뉴스 분류기다. 각 헤드라인에 해당하는 토픽을 고른다.
규칙:
- 주어진 토픽 목록에서만 고른다.
- 한 헤드라인에 여러 토픽이 해당될 수 있다. 해당 없으면 빈 배열.
- 추측하지 않는다. 헤드라인에 근거가 없으면 넣지 않는다.
- 반드시 JSON 배열만 출력한다. 설명 금지.
출력 형식: [{"i": 0, "topics": ["실적"]}, {"i": 1, "topics": []}]"""

WRITE_SYSTEM = """당신은 계량금융 연구자의 일간 시장 기록을 대신 쓴다.

문체 규칙:
- 평서형 종결('~다'). 존댓말·구어체 금지.
- 주어진 숫자만 쓴다. 제공되지 않은 수치를 만들어내지 않는다.
- 인과 주장 금지. "A 때문에 B가 올랐다"가 아니라 "A가 보도된 날 B의 잔차가 +Xbp였다"로 쓴다.
- 전망·권유 금지. 매수/매도/목표가를 언급하지 않는다.
- 금지 표현: 혼조세, 관망세, 눈치보기, 훈풍, 온기, 주목된다, 전망된다, 기대된다.
- 이모지·불릿 금지. 3~5개 문장의 연속된 산문.
- 설명되지 않는 움직임은 "미설명"이라고 명시한다."""


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, cfg):
        import anthropic

        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
        self.classify_model = cfg.get_path("llm.classify_model", "claude-haiku-4-5-20251001")
        self.write_model = cfg.get_path("llm.write_model", "claude-sonnet-5")

    def classify_topics(self, headlines: Sequence[str], topics: Sequence[str]) -> list[list[str]]:
        if not headlines:
            return []
        results: list[list[str]] = [[] for _ in headlines]
        BATCH = 40
        for s in range(0, len(headlines), BATCH):
            chunk = list(headlines[s:s + BATCH])
            payload = "\n".join(f"{i}. {h}" for i, h in enumerate(chunk))
            prompt = (f"토픽 목록: {json.dumps(list(topics), ensure_ascii=False)}\n\n"
                      f"헤드라인:\n{payload}")
            try:
                msg = self.client.messages.create(
                    model=self.classify_model,
                    max_tokens=2000,
                    system=CLASSIFY_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = msg.content[0].text.strip()
                text = text[text.find("["):text.rfind("]") + 1]
                for item in json.loads(text):
                    i = int(item.get("i", -1))
                    if 0 <= i < len(chunk):
                        results[s + i] = [t for t in item.get("topics", []) if t in topics]
            except Exception as e:
                log.warning("Anthropic 분류 실패(배치 %d): %s -- 룰베이스로 폴백", s, e)
                from .rule_provider import RuleProvider

                fb = RuleProvider().classify_topics(chunk, topics)
                for i, v in enumerate(fb):
                    results[s + i] = v
        return results

    def write_narrative(self, context: dict) -> str:
        prompt = (
            "다음은 미국 증시 한 거래일의 집계 결과다. 이 숫자만 사용해 "
            "3~5문장의 귀인 서술을 작성하라.\n\n"
            f"```json\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n```"
        )
        try:
            msg = self.client.messages.create(
                model=self.write_model,
                max_tokens=900,
                system=WRITE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.warning("Anthropic 원고 생성 실패: %s -- 룰베이스로 폴백", e)
            from .rule_provider import RuleProvider

            return RuleProvider(self.cfg).write_narrative(context)
