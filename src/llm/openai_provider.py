"""OpenAI API 어댑터. 인터페이스는 Anthropic 쪽과 동일하다."""
from __future__ import annotations

import json
import logging
from typing import Sequence

from ..config import env
from .anthropic_provider import CLASSIFY_SYSTEM, WRITE_SYSTEM
from .base import LLMProvider, build_narrative_prompt

log = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, cfg):
        from openai import OpenAI

        self.cfg = cfg
        self.client = OpenAI(api_key=env("OPENAI_API_KEY"))
        self.classify_model = cfg.get_path("llm.openai_classify_model", "gpt-4o-mini")
        self.write_model = cfg.get_path("llm.openai_write_model", "gpt-4o")

    def _chat(self, model: str, system: str, user: str, max_tokens: int) -> str:
        r = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return (r.choices[0].message.content or "").strip()

    def classify_topics(self, headlines: Sequence[str], topics: Sequence[str]) -> list[list[str]]:
        if not headlines:
            return []
        results: list[list[str]] = [[] for _ in headlines]
        BATCH = 40
        for s in range(0, len(headlines), BATCH):
            chunk = list(headlines[s:s + BATCH])
            payload = "\n".join(f"{i}. {h}" for i, h in enumerate(chunk))
            user = (f"토픽 목록: {json.dumps(list(topics), ensure_ascii=False)}\n\n"
                    f"헤드라인:\n{payload}")
            try:
                text = self._chat(self.classify_model, CLASSIFY_SYSTEM, user, 2000)
                text = text[text.find("["):text.rfind("]") + 1]
                for item in json.loads(text):
                    i = int(item.get("i", -1))
                    if 0 <= i < len(chunk):
                        results[s + i] = [t for t in item.get("topics", []) if t in topics]
            except Exception as e:
                log.warning("OpenAI 분류 실패(배치 %d): %s -- 룰베이스로 폴백", s, e)
                from .rule_provider import RuleProvider

                for i, v in enumerate(RuleProvider().classify_topics(chunk, topics)):
                    results[s + i] = v
        return results

    def write_narrative(self, context: dict) -> str:
        user = build_narrative_prompt(context)
        try:
            return self._chat(self.write_model, WRITE_SYSTEM, user, 1400)
        except Exception as e:
            log.warning("OpenAI 원고 생성 실패: %s -- 룰베이스로 폴백", e)
            from .rule_provider import RuleProvider

            return RuleProvider(self.cfg).write_narrative(context)
