"""LLM 프로바이더 추상 인터페이스.

파이프라인은 프로바이더를 몰라야 한다. 키가 하나도 없어도 RuleProvider로
전체 파이프라인이 끝까지 돌아가야 한다 -- 그래야 로직 버그와 API 문제를 분리해서
디버깅할 수 있다.
"""
from __future__ import annotations

import abc
from typing import Sequence


class LLMProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def classify_topics(self, headlines: Sequence[str], topics: Sequence[str]) -> list[list[str]]:
        """각 헤드라인에 대해 해당하는 토픽 리스트를 반환. 다중 라벨 허용."""

    @abc.abstractmethod
    def write_narrative(self, context: dict) -> str:
        """리포트 2번 블록(귀인 서술) 한국어 본문 생성."""

    def available(self) -> bool:
        return True


def get_provider(cfg) -> LLMProvider:
    from ..config import env

    provider = str(cfg.get_path("llm.provider", "rule")).lower()

    if provider == "anthropic" and env("ANTHROPIC_API_KEY"):
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if provider == "openai" and env("OPENAI_API_KEY"):
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)

    from .rule_provider import RuleProvider

    return RuleProvider(cfg)
