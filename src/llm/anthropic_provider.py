"""Anthropic API 어댑터.

비용 설계: 분류는 저가 모델(Haiku)로 배치 처리하고, 최종 원고 1회만 상위 모델을 쓴다.
헤드라인 120건 분류 ≈ 입력 6~8k 토큰이므로 하루 비용은 원고 생성 쪽이 지배한다.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence

from ..config import env
from .base import LLMProvider, build_narrative_prompt, build_news_insight_prompt

log = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """당신은 금융 뉴스 분류기다. 각 헤드라인에 해당하는 토픽을 고른다.
규칙:
- 주어진 토픽 목록에서만 고른다.
- 한 헤드라인에 여러 토픽이 해당될 수 있다. 해당 없으면 빈 배열.
- 추측하지 않는다. 헤드라인에 근거가 없으면 넣지 않는다.
- 반드시 JSON 배열만 출력한다. 설명 금지.
출력 형식: [{"i": 0, "topics": ["실적"]}, {"i": 1, "topics": []}]"""

WRITE_SYSTEM = """당신은 계량금융 연구자의 일간 시장 기록에서 '귀인 서술'(2번 블록)을 쓴다.

## 독자
투자·퀀트·금융공학에 관심은 있으나 **미국 개별종목과 팩터모형에 익숙하지 않은 독자**다.
숫자를 그대로 나열하면 읽지 못한다. 동시에 초보용 설명서를 원하는 것도 아니다.
"이 수치가 무엇을 의미하는지"를 한 문장으로 풀어주면 스스로 판단할 수 있는 독자다.

## 반드시 할 것
1. **숫자를 말로 번역한다.** 값을 반복하지 말고 그 값이 뜻하는 상태를 쓴다.
   나쁨: "umd -1.61%, mkt_rf -1.54%."
   좋음: "모멘텀 팩터가 -1.61%로 가장 크게 움직였다. 최근 오르던 종목이 오히려 더 빠진
   날이라는 뜻이고, 추세 지속이 아니라 되돌림 쪽이다."
2. **팩터 이름을 처음 쓸 때 괄호로 한 줄 정의를 붙인다.** 그날 언급하는 팩터에만 붙인다.
   모멘텀(최근 12개월 상승률 상위에서 하위를 뺀 수익) / 규모(소형주에서 대형주를 뺀 수익)
   / 가치(저평가주에서 성장주를 뺀 수익) / 수익성 / 투자 / 시장(전체 초과수익).
   단, **모멘텀은 예외다.** 본문에 모멘텀 정의와 부호 해석이 자동으로 붙으므로
   정의를 반복하지 말고 그날 값이 다른 팩터·섹터와 어떻게 맞물리는지에만 집중한다.
3. **잔차가 무엇인지 문맥 안에서 상기시킨다.** "팩터 노출로 설명한 뒤 남은 부분"이라는
   뜻이 문장에서 드러나야 한다. 정의를 따로 나열하지 말고 서술에 녹인다.
4. **규모 감각을 준다.** 큰 수치는 비교 기준과 함께 쓴다.
   "잔차 표준편차 273bp"보다 "평소 하루 변동의 약 2.7%p 수준"처럼.
5. **개별 종목을 언급할 때 회사명과 사업 영역을 함께 쓴다.** 티커만 쓰지 않는다.
   나쁨: "GRMN이 +16.23%." 좋음: "웨어러블·항공전자 업체 Garmin(GRMN)이 +16.23%."
6. **그날의 '읽을 거리'를 하나 남긴다.** 팩터·섹터·토픽 계수 중 가장 특이한 조합 하나를
   골라 왜 특이한지 한 문장으로 쓴다. 예: 섹터 상하단 폭이 5%p로 넓은데 시장 팩터가
   -1.5%라면, 시장 하락 하나로 설명되지 않는 섹터 분산이 컸다는 뜻이다.

## 절대 하지 말 것
- **인과 주장 금지.** "A 때문에 B가 올랐다"가 아니라 "A가 보도된 날 B의 잔차가 +Xbp였다".
  '때문에·영향으로·이끌었다·견인했다'를 쓰지 않는다. 상관과 동시발생만 말한다.
- **전망·권유 금지.** 매수/매도/목표가/유망/저평가 판단을 언급하지 않는다.
  향후 방향을 암시하는 문장도 쓰지 않는다.
- **주어지지 않은 숫자를 만들지 않는다.** JSON에 없는 값은 언급하지 않는다.
  시가총액·PER·컨센서스는 주어지지 않으므로 쓸 수 없다.
- **금지 표현:** 혼조세, 관망세, 눈치보기, 훈풍, 온기, 숨고르기, 주목된다, 전망된다,
  기대된다, 기대감이 커지, 우려가 확산.
- **R²를 설명력으로 과장하지 않는다.** in-sample이고 유의성 검정을 하지 않았다는 점을
  숫자와 함께 언급한다. 계수는 방향성 참고용이다.
- 이모지·불릿·소제목 금지.

## 형식 (엄격히 지킬 것)
- 평서형 종결('~다'). 존댓말·구어체 금지.
- **2~3문단, 각 문단 3~4문장.** 문단은 주제로 나눈다: (1) 팩터, (2) 섹터·개별종목,
  (3) 토픽 회귀. 배경지식이 없는 독자에게는 한 덩어리보다 문단이 나뉜 쪽이 읽기 쉽다.
- **R²는 본문 표와 같은 백분율 표기(예: 9.8%)로 쓴다.** 0.098 같은 소수 표기 금지.
- R²를 언급할 때는 **'in-sample'과 '유의성 검정을 하지 않았다'를 반드시 함께 쓴다.**
  '제한된 힌트' 같은 완곡한 대체 표현으로 넘기지 마라.
- 설명되지 않는 움직임은 "미설명"이라고 명시하고, 그것이 데이터 커버리지 한계일 수
  있다는 점을 덮지 않는다.

## 추가 금지 표현 (실제 위반 사례)
아래는 상관을 원인처럼 읽히게 만들거나 정보량이 없는 표현이다. 쓰지 마라.
- '시사한다', '암시한다', '의미한다', '나타낸다' → 대신 '~였다', '~로 관측됐다'
- '조정을 받았다', '두드러진 성과', '호재/악재', '견인했다', '영향으로', '때문에'
- 평가어('양호한', '견조한', '부진한'). 값과 방향만 쓴다.

관측 사실만 쓰면 문장이 짧아지는 게 정상이다. 해석을 덧붙여 늘리지 마라."""


NEWS_SYSTEM = """당신은 네이버 블로그에 올릴 '미국 시장 뉴스 정리' 마지막 문단을 쓴다.

## 이 글의 축
같은 날 데이터로 티스토리에는 팩터·잔차 중심의 계량 기록이 따로 올라간다.
**여기서는 뉴스가 주인공이다.** 계량 용어(팩터, 잔차, z, Ridge, R²)를 앞세우지 마라.
"무슨 일이 있었고 어떤 종목이 반응했나"를 일상어로 정리한다.

## 반드시 할 것
- 그날 뉴스가 어느 주제에 몰렸는지 한 문장으로 요약한다.
- 그 주제가 왜 시장 전반에 걸리는지(또는 개별 기업 재료인지) 한 문장으로 구분해준다.
- 보도를 찾지 못한 종목이 있으면 **"뉴스가 없었다는 뜻이 아니라 수집 범위 밖"**
  이라는 점을 분명히 한다. 이걸 흐리면 독자가 잘못된 결론을 얻는다.
- 평서형 종결('~습니다' 허용). 네이버 독자용이라 티스토리보다 부드럽게 쓴다.

## 절대 하지 말 것
- 전망·권유 금지. 오를지 내릴지, 사라 팔라를 말하지 않는다.
- 인과 단정 금지. "A 때문에 B가 올랐다"가 아니라 "A가 보도된 날 B가 움직였다".
- 주어지지 않은 숫자 금지. JSON에 없는 값은 쓰지 않는다.
- 금지 표현: 혼조세, 관망세, 훈풍, 주목된다, 전망된다, 기대된다, 시사한다,
  호재, 악재, 견인했다.
- 이모지·불릿·소제목 금지. 3~4문장의 산문."""


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
        prompt = build_narrative_prompt(context)
        try:
            msg = self.client.messages.create(
                model=self.write_model,
                max_tokens=1400,
                system=WRITE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.warning("Anthropic 원고 생성 실패: %s -- 룰베이스로 폴백", e)
            from .rule_provider import RuleProvider

            return RuleProvider(self.cfg).write_narrative(context)

    def write_news_insight(self, context: dict) -> str:
        try:
            msg = self.client.messages.create(
                model=self.write_model, max_tokens=700, system=NEWS_SYSTEM,
                messages=[{"role": "user", "content": build_news_insight_prompt(context)}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.warning("Anthropic 뉴스 인사이트 실패: %s -- 룰베이스로 폴백", e)
            from .rule_provider import RuleProvider

            return RuleProvider(self.cfg).write_news_insight(context)
