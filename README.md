# us-market-daily

미국 시장의 하루를 **관측치 하나**로 기록하는 파이프라인.

매일 데이터를 수집하고, 위험조정 잔차를 계산하고, 뉴스 토픽으로 귀인한 뒤,
**전날 세운 가설을 그날 결과로 공개 채점한다.**

> **예측을 팔지 않고 기록을 남긴다.**
> 어떤 종목이 오를지 말하지 않는다. 대신 "뉴스 감성이 익일 초과수익을 설명하는가"라는
> 채점 가능한 가설을 세우고, 매일 실현값으로 채점해 **맞은 날과 틀린 날을 모두 그대로 둔다.**

시장 해설은 대개 사후에 이유를 붙이기 때문에 틀렸다고 말할 수 없다. 이 저장소는
반대로 간다 — 가설을 먼저 고정하고, 코드와 데이터를 공개하고, 결과를 지우지 않는다.
시간이 지나면 실시간 out-of-sample 기록이 쌓인다.

- 발행처: https://dailyresidualnote.com
- 운영자: 디지털금융·핀테크·블록체인 전공 박사과정
- 독자 대상: 퀀트·계량금융·데이터 분석을 하는 사람

## 지금 상태

| 항목 | 상태 |
|---|---|
| 파이프라인 | 동작. 오프라인 테스트 7종 통과 |
| 데이터 | 가격 525종목 179,991행 (2025-03~2026-07) + 매크로·팩터·뉴스 |
| 자동화 | GitHub Actions 일간/주간 cron 동작 확인 |
| 1단계 (수집·기록) | 완료 |
| 2단계 (Fama-MacBeth, event study, 유의성 검정) | 누적 60거래일 이후 |

## 리포트 5블록

| 블록 | 답하는 질문 | 규칙 |
|---|---|---|
| 1. 무엇이 움직였나 | 지수·섹터·금리·변동성 | 숫자만. 형용사 없음 |
| 2. 무엇이 설명했나 | 공통 요인으로 설명되나 | 토픽 노출 Ridge. **인과 주장 금지** |
| 3. 설명되지 않은 움직임 | 팩터로 안 되는 종목과 뉴스 | 설명 안 되면 "미설명"으로 남김 |
| 4. **어제 신호 채점** | 전날 가설이 맞았나 | **이 프로젝트의 존재 이유** |
| 5. 다음 거래일 일정 | 해석이 어려워질 날인가 | 전망 아님. FRED 릴리스 캘린더 |

5번 블록은 전망이 아니다. FOMC나 물가지표 발표일에는 전 종목의 잔차가 같은 방향으로
움직여 3번 블록의 개별 해석이 오염된다. 그런 날을 미리 표시해 두면 2단계에서
event-date clustering을 따로 취급할 수 있다.

`builder.BANNED`가 정보량 0인 표현과 약한 인과 표현을 출력 단계에서 검사한다
(혼조세, 관망세, 시사한다, 견인했다 …). 검출되면 붙여넣기 패키지의 체크리스트에
실려 나가 사람이 고치게 한다.

## 방법론

전체 설명은 **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** — 배경지식이 없는 독자
기준으로 쓴 13개 절이다. 모형 목록은 [docs/MODELS.md](docs/MODELS.md).

### 위험모형: FF5 + Carhart UMD

**예측 모형이 아니라 귀인 모형이다.** 알려진 위험 노출로 설명되는 부분을 걷어내
잔차 `e_it`를 남기는 도구다. 뉴스가 설명해야 하는 대상은 수익률이 아니라 이 잔차다.

모멘텀을 더하는 이유가 중요하다. FF5에는 모멘텀이 없어서, 빼놓으면 모멘텀 노출이
잔차에 그대로 남고 **그걸 뉴스 효과로 오인하게 된다.** HML은 Fama-French(2015)가
미국 데이터에서 상당 부분 redundant라고 보고했으므로 계수 해석을 보수적으로 한다.

### look-ahead 차단 두 지점

1. 뉴스 창을 `[전일 16:00 ET, 당일 16:00 ET)`로 자른다
2. 베타 추정창에서 **당일을 배제**한다 (`f.index < session`)

당일을 포함하면 그날 뉴스 충격이 베타에 흡수되어 잔차가 축소된다. 정작 찾으려는
것이 사라진다.

### 채점의 통계 설계

주간 회고에서 과잉 주장을 막기 위해 세 가지를 강제한다.

- **Newey-West(HAC) t** — 일별 스프레드는 자기상관이 있다. OLS 표준오차는 t를 부풀린다
- **이항검정 p** — "적중률 60%"만으로는 표본이 5일인지 200일인지 알 수 없다
- **|t| > 3.0 허들** — Harvey, Liu & Zhu(2016)는 다중검정을 고려하면 관행적 2.0
  기준으로 우연히 유의한 결과가 대량 생산된다고 지적했다. 매주 여러 스펙을
  들여다보는 이 프로젝트는 정확히 그 함정에 취약하다

판정은 4단계로만 나온다: `판단 보류` / `귀무가설 기각 실패` / `결정적이지 않음` /
`기각 실패 (신호 존재 가능)`. 표본 20거래일 미만이면 무조건 판단 보류다.

## 첫 실제 실행에서 드러난 것

이 저장소에서 가장 재사용 가치가 높은 부분이라고 생각한다. 2026-07-30 첫 실운영에서
버그를 여러 건 찾았는데, **분류가 중요하다.**

| 증상 | 원인 | 발현 |
|---|---|---|
| 유니버스가 ETF 25개로 축소 | `pd.read_html(url)`이 urllib 사용 → macOS 인증서 실패 | 경고 1줄 |
| UMD 전량 결측 (모멘텀 통제 소실) | Ken French 모멘텀 파일 줄 끝 쉼표 → 컬럼 수 불일치 | 경고 1줄 |
| RSS 6개 피드 전멸 | `feedparser.parse(url)`도 urllib · BLS는 일반 UA를 403 | 경고 6줄 |
| **2번 블록이 통째로 사라짐** | novelty가 자기 자신과 비교되어 0 → `sentiment_w`=0 → 토픽 행렬 전부 0 | **무증상** |
| AV 토픽 1058건 폐기 + LLM 호출 낭비 | parquet 왕복 시 list→ndarray, `isinstance` 검사가 조용히 False | **무증상** |
| 1번 블록이 "10Y +0bp" 허위 보고 | `ffill` 후 두 행 비교 → FRED 공개 지연 시 변화량이 0 | **무증상** |
| 매크로 차트 선 끊김 + 거짓 '거래일' | 달력일로 잘라 30%가 결측인 구간을 그림 | **무증상** |
| 스케줄 실행만 AV 없이 동작 | 워크플로 env에 `ALPHAVANTAGE_API_KEY` 누락 | **무증상** |
| Garmin 실적 기사가 '통화정책'으로 분류 | AV 토픽에 뉴스 주제와 산업 섹터가 섞여 있음 | **무증상** |

**무증상 쪽이 훨씬 위험하다.** 예외를 던지지 않고 그럴듯한 리포트를 계속 생산하므로
몇 주간 모르고 갈 수 있었고, 그동안 기존 테스트 4종은 전부 통과하는 상태였다.

교훈 세 개.

- **URL을 직접 받아주는 편의 API를 쓰지 않는다.** `pd.read_html(url)`,
  `feedparser.parse(url)`은 urllib으로 나가고 `requests`는 certifi로 나간다.
  이 프로젝트는 requests로 통일한다.
- **parquet 왕복 후 타입이 바뀐다.** 리스트 열은 ndarray로 돌아온다.
  `storage.as_list()`를 거칠 것. `x or []`·`isinstance(x, list)`·`if x:` 금지.
- **"경고 없음"은 정상의 증거가 아니다.** 무증상 건들은 전부 산출물을 직접 읽어서
  찾았다. 매일 리포트를 눈으로 확인하는 루틴이 QA를 겸한다.

회귀 테스트: `tests/test_collect_parsing.py`, `tests/test_silent_failures.py`.
둘 다 외부망 없이 합성 데이터로 돈다.

## 뉴스 태깅 — 가장 큰 데이터 품질 병목

RSS만 쓰면 종목 태깅이 부정확하다. 회사명 부분일치는 오탐이 많고, 티커 대문자
매칭은 일반 단어와 충돌한다(A, ALL, CAT, KEY, ON, IT).

Alpha Vantage `NEWS_SENTIMENT`가 `ticker_sentiment` 배열에 종목별 **relevance_score**를
담아준다. 이게 태깅 문제를 근본적으로 해결한다.

### 실측으로 확인한 것 (2026-07-30, 표본 1거래일)

`scripts/diagnose_news.py`가 이 분석을 재현한다.

**relevance_min 0.25는 아무것도 걸러내지 않는다.** AV가 돌려준 relevance의 최소값이
0.301이었다(669쌍, 중위 1.000). 0~0.30 사이 어떤 값을 넣어도 결과가 같다. 가장
불확실하다고 지목했던 파라미터가 병목이 아니었다.

**진짜 병목은 토픽 배치였다.** 이례치 61종목을 갈라보니 (a) 태그됨 31, (b) 언급은
있으나 relevance 미달 **0**, (c) AV 응답에 아예 없음 30이었다. (b)가 0이므로
임계값 문제가 아니다.

**대형주 편향 가설은 기각됐다.** 거래대금 중위가 매칭 405M vs 미설명 468M,
Mann-Whitney p=0.638. 미설명 종목이 더 작지 않다. 원인은 섹터별 커버율 격차였고
(Consumer Staples 23.5% ~ Info Tech 54.1%), 요청하지 않은 토픽의 섹터가 구조적으로
빈다. 토픽 배치를 4→7개로 늘리자:

| | 배치 4개 | 배치 7개 |
|---|---|---|
| 유니버스 커버율 | 39.8% | **51.9%** |
| 이례치 매칭 | 31/61 | **41/61** |
| 미설명 | 49.2% | **32.8%** |

새로 설명된 종목이 CNP·ETR·EVRG·LNT·NI(유틸리티), FANG·EXE(에너지), GEHC 등이었다.
**남은 32.8%도 아직 실제 발견으로 읽으면 안 된다.**

무료 티어가 25요청/일·5요청/분이라 종목별 호출은 불가능하다. 토픽 배치로 7회만
부른다(`limit=1000`이라 한 번에 많이 받는다).

### AV 토픽 매핑 주의

AV 토픽에는 **뉴스 주제와 산업 섹터가 섞여 있다.** `financial_markets`(금융시장 일반)를
통화정책으로 매핑했더니 Garmin 실적 기사가 '통화정책' 대표 헤드라인이 됐다. 의미가
정확히 일치하는 3개(`economy_monetary`, `earnings`, `mergers_and_acquisitions`)만
쓰고 나머지는 LLM 분류로 보낸다. 섹터 배치를 계속 요청하는 것과는 별개다 — 그건
종목 커버리지를 위한 것이고 토픽 라벨로 쓰지 않을 뿐이다.

원시 슬러그를 `av_topics_raw`로 함께 저장한다. 매핑만 저장하면 나중에 매핑을 고쳐도
과거 데이터를 재계산할 수 없어 재수집이 강제된다(실제로 겪었다).

AV 자체 감성 점수는 산출 방식이 비공개라 보조 지표로만 저장하고, 주 분석은 재현
가능한 Loughran-McDonald 사전을 쓴다.

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env          # FRED_API_KEY만 넣어도 동작한다
```

```bash
# 1) 최초 세팅 — venv, 의존성, 한글 폰트 확인, 오프라인 테스트 7종
bash scripts/bootstrap.sh
```

```bash
# 2) 과거 데이터 적재 (베타 추정에 250거래일 필요, 최초 1회)
python scripts/run_daily.py --backfill 500
```

```bash
# 3) 일간 실행
python scripts/run_daily.py
python scripts/run_daily.py --session 2026-07-24    # 특정 거래일
python scripts/run_daily.py --dry-run               # 수집 없이 리포트만
```

```bash
# 4) 주간 회고 (채점 5일 이상 쌓인 뒤)
python scripts/run_weekly.py --next "다음 주 가설 문장"
```

## 스크립트

| 스크립트 | 주기 | 용도 |
|---|---|---|
| `run_daily.py` | 매일 | 수집 → 잔차 → 리포트 → 3채널 산출물 |
| `link_post.py` | 발행 직후 | 글 번호 기록 (15초) |
| `run_weekly.py` | 주 1회 | **가설 검증 회고** |
| `run_analytics.py` | 주 1회 | 독자 유입 분석 (발행 안 함) |
| `check_site.py` | 스킨 수정 후 | 배포된 HTML로 GA4·canonical·sitemap 검증 |
| `diagnose_news.py` | 필요 시 | relevance 임계값·미설명 원인 분해 |
| `make_notice.py` | 방법론 수정 시 | 고정 페이지 패키지 생성 |

**일간은 기록이고 주간이 본선이다.** 자동 생성 리포트를 매일 올려도 사람이 매일
읽지는 않는다. 일간의 가치는 채점이 사후 조작 불가능해진다는 데 있고, 실제로 읽히는
글은 "이번 주 신호가 작동했는가"다.

GA4·서치콘솔·사이트맵 설치 절차는 [docs/SETUP_ANALYTICS.md](docs/SETUP_ANALYTICS.md).
블로그 관리자 작업은 손으로 하고 **결과 검증만 자동화**했다 — `check_site.py`가 실제
배포된 HTML을 받아 대조한다. 특히 티스토리는 모바일 요청을 `/m/`의 별도 시스템 스킨으로
보내서, 스킨에 넣은 태그가 모바일에서 실행되지 않는 함정이 있다(실측 확인).

## 3채널 구조

한 번 실행하면 세 채널 산출물이 동시에 나온다. **역할과 소재가 다르다.**

| 채널 | 역할 | 자동화 | 소재 |
|---|---|---|---|
| **GitHub** | 코드·데이터·아카이브 | **완전 자동** | 전문 + 원본 데이터 |
| **티스토리** | 본진 | 붙여넣기 1분 | 팩터·잔차 중심 계량 기록 |
| **네이버** | 다른 독자층 | 붙여넣기 4분 | **글 2개** — 마감 숫자 요약 + 뉴스 리포트 |

네이버에는 **검색 의도가 다른 글 두 개**를 올린다. 한 글에 합치면 앞부분이 숫자면
뉴스 독자가 이탈하고, 앞부분이 서술이면 검색 스니펫에 숫자가 안 잡힌다.

| | 대응 쿼리 | 내용 | 발행 |
|---|---|---|---|
| `1_summary` | "미국증시 마감" | 지수·섹터·금리·이례치·채점. 스캔용 | 07:10 |
| `2_news` | "무슨 일이 있었나" | 주제별 뉴스 지형, 보도 확인/미확인 종목 | 12:00 이후 |

둘 다 티스토리의 요약본이 아니다. 티스토리는 팩터·잔차가 주인공이고 네이버는
숫자와 뉴스가 주인공이라 문장이 겹치지 않는다. 같은 날 두 글을 연달아 올리면
유사문서로 묶일 수 있어 반나절 벌린다.

```
posts/2026-07-29.md          GitHub 아카이브 (front matter + canonical_url)
assets/2026-07-29/*.png
data/scorecard.json          누적 성적표

out/2026-07-29/             붙여넣기 패키지. 텍스트만 커밋된다(하루 ~60KB)
├── tistory/
│   ├── title.txt
│   ├── post.html            HTML 모드 붙여넣기용. 차트가 raw URL로 인라인됨
│   ├── tags.txt
│   └── README.txt           게시 절차 + 금지 표현 검출 결과
└── naver/
    ├── README.txt          두 글의 발행 순서·시간차
    ├── 1_summary/          마감 숫자 요약 (title/post/tags)
    └── 2_news/             뉴스 리포트 (title/post/tags)

이미지는 패키지에 복사하지 않는다. 티스토리는 raw URL로 인라인되어 파일이 필요
없고, 네이버는 assets/{날짜}/ 에서 첨부하면 된다. 중복 저장하면 하루 660KB씩
저장소가 커진다.
```

### 왜 붙여넣기인가

**두 블로그 모두 API가 죽었다.** 네이버 글쓰기 API는 2020-05-06 종료(사유가 정확히
"광고성 대량 생산 포스팅 방지"), 티스토리 Open API는 2024년 2월 완전 종료.

브라우저 자동화로 우회하지 않는다. 두 플랫폼이 API를 죽인 이유가 정확히 자동 대량
포스팅 방지이고, 계정 정지는 계정 단위라 복구가 어렵다. **붙여넣기 직전까지만**
자동화한다.

### 매일 루틴 (3분)

1. `git pull` — `out/{날짜}/` 에 붙여넣기 패키지가 들어 있다
2. `tistory/post.html` → HTML 모드 붙여넣기 → **2번 블록 한두 문장 손보기** → 발행
3. 글 번호 기록 — `python scripts/link_post.py https://dailyresidualnote.com/123`
4. `naver/1_summary/` → 붙여넣기 → 07:10 예약 (원문이 먼저 색인되도록)
5. `naver/2_news/` → 붙여넣기 → 12:00 이후 예약 (두 글을 붙여 올리지 않는다)

**2번 블록을 손보는 단계를 빼지 말 것.** 매일 실제로 다르게 나오는 유일한 부분이고,
LLM이 남기는 문체 위반(약한 인과, 평가어)을 여기서 고친다. 검출된 표현이
`tistory/README.txt`에 실려 나온다.

이미지 드래그 단계는 없앴다. 차트를 GitHub raw URL로 인라인하기 때문이다. 대신
**Actions가 그날 커밋을 push한 뒤에만 이미지가 보이고**, 레포를 private으로 바꾸거나
이름을 바꾸면 과거 글 이미지가 전부 깨진다.

### 숫자형 포스트 주소 대응

티스토리 포스트 주소를 '숫자'로 두면 URL(`/123`)이 **발행하는 순간** 결정된다.
문자 주소는 한글 제목이 퍼센트 인코딩 범벅이 되므로 숫자를 유지하고, 발행 후
`link_post.py`로 한 번 기록한다. 이게 `canonical_url`, 네이버 원문 링크,
애널리틱스 경로 매핑을 한꺼번에 맞춘다. 멱등이다.

```bash
python scripts/link_post.py --list
python scripts/link_post.py 123
python scripts/link_post.py https://.../200 --kind weekly
```

## 필요한 키

| 키 | 필수 | 발급 |
|---|---|---|
| `FRED_API_KEY` | 사실상 필수 | https://fredaccount.stlouisfed.org/apikeys (무료·즉시) |
| `ALPHAVANTAGE_API_KEY` | 강력 권장 | https://www.alphavantage.co/support/#api-key (무료·즉시) |
| `SEC_USER_AGENT` | EDGAR 쓸 때 | 형식: `프로젝트명 이메일` (미기재 시 403) |
| `OPENAI_API_KEY` | 선택 | `llm.provider: openai` 일 때. 없으면 룰베이스 폴백 |
| `GA4_PROPERTY_ID` 외 | 선택 | 없으면 CSV 폴백 |

**키가 하나도 없어도 파이프라인은 끝까지 돌아간다.** 의도한 설계다 — 로직 버그와
API 문제를 분리해서 디버깅할 수 있어야 한다. SDK 미설치·초기화 실패도 룰베이스로
폴백한다. 리포트가 밋밋해지는 것과 그날 기록이 아예 없는 것은 비교할 문제가 아니다.

`llm.provider`를 바꿀 때는 `requirements.txt`의 해당 SDK 주석도 함께 풀어야 한다.
CI가 이 파일로 설치하므로, 로컬만 설치된 상태면 스케줄 실행에서만 조용히 폴백한다.

## 구조

```
scripts/
├── run_daily.py          매일. 수집 -> 잔차 -> 리포트 -> 3채널
├── link_post.py          발행 직후. 글 번호 기록
├── run_weekly.py         주 1회. 가설 검증 회고
├── run_analytics.py      주 1회. 유입 분석
├── check_site.py         배포된 HTML로 측정 설치 검증
├── diagnose_news.py      뉴스 태깅 진단
└── make_notice.py        고정 페이지 패키지

src/
├── config.py             설정 + .env 로더
├── calendar_utils.py     거래일·DST·뉴스창 (look-ahead 차단)
├── storage.py            append-only parquet upsert + as_list
├── collect/
│   ├── prices.py         yfinance, 배당조정 수익률
│   ├── macro.py          FRED. 시리즈별 공개 지연을 그대로 보고
│   ├── factors.py        Ken French + ETF 프록시 2단 구조
│   ├── news.py           RSS + SEC EDGAR + 종목 태깅
│   ├── news_alphavantage.py  relevance_score 기반 정밀 태깅
│   ├── calendar_events.py    FRED 릴리스 캘린더 (5번 블록)
│   └── analytics.py      GA4 + AdSense (읽기 전용, CSV 폴백)
├── process/
│   ├── residual.py       FF5+UMD 롤링 베타, Vasicek 축소
│   ├── sentiment.py      Loughran-McDonald 사전, novelty
│   ├── attribution.py    섹터·팩터 분해, 토픽 Ridge, 스코어카드
│   └── weekly_stats.py   Newey-West t, 이항검정, HLZ 허들
├── llm/                  프로바이더 어댑터 (rule/openai/anthropic)
├── report/
│   ├── charts.py         일간 4종, 한글 폰트 자동 탐색
│   ├── builder.py        일간 5블록 마크다운 + BANNED 검사
│   ├── weekly_*.py       누적 곡선, 6단 회고
│   └── analytics_report.py
└── publish/
    ├── github_archive.py 완전 자동
    ├── tistory_package.py 계량 기록 HTML + 이미지 인라인
    ├── naver_package.py   뉴스 중심 평문 리포트
    ├── url_registry.py    숫자형 주소 <-> 날짜 매핑
    └── site_audit.py      배포된 사이트 점검

docs/
├── METHODOLOGY.md        독자용 방법론 안내 (공지글 원본, 단일 출처)
├── MODELS.md             사용 모형 전체 목록
├── POST_BACKLOG.md       개념·방법론 글 백로그 (72편) + 참고문헌 41편
└── SETUP_ANALYTICS.md    GA4·서치콘솔·사이트맵 설치 절차
```

## 시간 설계

GitHub Actions cron은 UTC만 받는데, 미국 서머타임 때문에 16:00 ET의 UTC 시각이
여름 20:00 / 겨울 21:00로 1시간 움직인다. 그래서 **cron을 KST로 고정하지 않고**
`calendar_utils.last_completed_session()`이 "직전 거래일 마감이 실제로 끝났는가"를
판정한다. NYSE 휴장일은 `pandas_market_calendars`로 처리한다.

목표는 07:00 KST 발행. Actions 스케줄은 러너 혼잡 시 지연될 수 있으므로 정각이
중요하면 블로그 예약 발행을 함께 쓴다.

## 검증

```bash
bash scripts/bootstrap.sh    # 7종 전부 실행
```

전부 **외부망 없이** 돈다. 알려진 정답을 심고 되찾는 방식이다.

| 테스트 | 확인하는 것 |
|---|---|
| `test_pipeline` | 합성 패널에 심은 베타를 잔차 추정이 되찾는가 (MAE 0.066) |
| `test_publish` | 3채널 산출물 구조, 채널 간 canonical 정합성 |
| `test_weekly_analytics` | 주간 통계, 유입 교차분석 |
| `test_url_registry` | 숫자형 주소에서 애널리틱스가 동작하는가 |
| `test_site_audit` | 배포 점검 로직 (티스토리 해시 오탐 방지 포함) |
| `test_collect_parsing` | 수집기 파싱 회귀 (French 줄끝 쉼표, macOS SSL, RSS UA) |
| `test_silent_failures` | **무증상 실패 회귀** (novelty 자기비교, parquet 타입, FRED 지연) |

## 알려진 한계

| 한계 | 내용 |
|---|---|
| 뉴스 커버리지 | 무료 소스는 모든 종목을 다루지 않는다. **"뉴스로 설명 안 됨"과 "뉴스를 못 구함"은 다르다.** 구분해서 표기한다 |
| 팩터 데이터 지연 | Ken French 일간 파일이 수 주 지연된다. 당일은 ETF 프록시로 근사하고 확정치가 오면 upsert로 소급 교체한다. 프록시는 서술용이고 계수 추정에는 확정치를 쓴다 |
| 생존편향 | 현재 지수 구성종목을 쓴다. `snapshot_date`로 매일 저장 중이므로 시간이 지나면 시점별 명단을 쓸 수 있다 |
| 지표 vintage 부재 | FRED 잠정치가 확정치로 덮인다. "발행 시점에 무엇을 보고 있었나"는 재현되지 않는다 (ALFRED 필요) |
| 일간 빈도 | 장중 반응은 분 단위인데 종가로 보면 상당 부분 씻긴다. intraday로 내려가면 마이크로구조 노이즈가 새 문제로 등장한다 |
| 토픽 분류 | AV 버킷이 넓고 LLM도 헤드라인만 본다. 오분류가 남아 있다 |

## 면책

본 저장소의 산출물은 공개 데이터를 자동 수집·분석한 **연구 기록**이며 투자자문이
아니다. 특정 종목의 매수·매도를 권유하지 않으며, 모든 수치는 발행 시점 기준이고
사후 수정될 수 있다.
