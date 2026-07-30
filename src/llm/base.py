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


def build_narrative_prompt(context: dict) -> str:
    """귀인 서술용 user 프롬프트. anthropic·openai가 공유한다.

    시스템 프롬프트가 "팩터 이름에 한 줄 정의를 붙여라", "회사명과 사업 영역을 써라"를
    요구하므로, **그 재료를 payload에 실어야 한다.** 안 실으면 모델이 정의를
    기억에서 만들어내고(환각) 프로젝트가 쓰는 정의와 어긋난다. 특히 팩터의 롱숏
    방향은 부호 해석을 바꾸므로 반드시 우리 쪽 정의를 준다.

    cross_section의 top/bottom에는 이미 name·sector가 들어 있다
    (residual.cross_section_stats가 구성종목 파일에서 붙인다).
    """
    import json

    from .rule_provider import FACTOR_KO, FACTOR_MEANING

    # 그날 실제로 등장한 팩터만 정의를 넘긴다. 전부 넘기면 모델이 안 쓴 팩터까지
    # 설명하려 든다.
    used = [k for k in (context.get("factors") or {}) if k in FACTOR_MEANING]
    ref = {FACTOR_KO[k]: FACTOR_MEANING[k] for k in used}

    return (
        "다음은 미국 증시 한 거래일의 집계 결과다. 이 숫자만 사용해 2번 블록(귀인 서술)을 "
        "작성하라.\n\n"
        "참고 — 팩터 정의(이 정의만 쓸 것, 임의로 바꾸지 말 것):\n"
        f"```json\n{json.dumps(ref, ensure_ascii=False, indent=2)}\n```\n\n"
        "집계 결과:\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        "cross_section.top / cross_section.bottom 의 각 항목에는 ticker 외에 name(회사명)과 "
        "sector가 들어 있다. 종목을 언급할 때 이 값을 함께 쓰고, 없는 사업 내용을 "
        "추측해서 붙이지 마라."
    )


def get_provider(cfg) -> LLMProvider:
    from ..config import env

    import logging

    log = logging.getLogger(__name__)
    provider = str(cfg.get_path("llm.provider", "rule")).lower()

    # SDK 미설치·초기화 실패를 여기서 흡수한다. 예전에는 예외가 그대로 올라가
    # 파이프라인이 죽었다. requirements.txt에서 openai/anthropic이 선택 의존성이라
    # **로컬은 설치되어 있고 CI는 아닌 상태가 실제로 발생한다.** 리포트가 조금
    # 밋밋해지는 것과 그날 기록이 아예 없는 것은 비교할 문제가 아니다.
    if provider == "anthropic" and env("ANTHROPIC_API_KEY"):
        try:
            from .anthropic_provider import AnthropicProvider

            return AnthropicProvider(cfg)
        except Exception as e:
            log.warning("anthropic 프로바이더 초기화 실패: %s -- 룰베이스로 폴백 "
                        "(pip install anthropic)", e)
    if provider == "openai" and env("OPENAI_API_KEY"):
        try:
            from .openai_provider import OpenAIProvider

            return OpenAIProvider(cfg)
        except Exception as e:
            log.warning("openai 프로바이더 초기화 실패: %s -- 룰베이스로 폴백 "
                        "(pip install openai)", e)
    elif provider in ("anthropic", "openai"):
        log.warning("llm.provider=%s 인데 API 키가 없다 -- 룰베이스로 폴백", provider)

    from .rule_provider import RuleProvider

    return RuleProvider(cfg)
