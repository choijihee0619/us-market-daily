# us-market-daily

미국 시장 하루를 한 개의 관측치로 기록하는 파이프라인. 매일 데이터를 수집하고,
위험조정 잔차를 계산하고, 뉴스 토픽으로 귀인한 뒤, 네이버 블로그에 붙여넣을
포스팅 패키지를 생성한다.

**핵심 설계 원칙: 예측을 팔지 않고 기록을 남긴다.** 4번 블록에서 어제 신호를
매일 공개 채점한다. 이게 이 프로젝트의 존재 이유이고, 동시에 실시간 out-of-sample
기록이 되어 연구 자산이 된다.

## 1단계 범위

수집 파이프라인 + 일간 리포트 자동 생성까지. 분석 모듈 (Fama-MacBeth, event study,
토픽 회귀 유의성 검정)은 2단계. 사용 모형 전체 목록은 [docs/MODELS.md](docs/MODELS.md).

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env          # FRED_API_KEY만 넣어도 동작한다

# 1) 오프라인 검증 — 외부 연결 없이 수학과 렌더링만 확인
python tests/test_pipeline.py
python tests/test_publish.py
python tests/test_weekly_analytics.py
python tests/test_url_registry.py
python tests/test_site_audit.py
python tests/test_collect_parsing.py

# 2) 과거 데이터 적재 (베타 추정에 250거래일 필요, 최초 1회)
python scripts/run_daily.py --backfill 500

# 3) 일간 실행
python scripts/run_daily.py
python scripts/run_daily.py --session 2026-07-24    # 특정 거래일
python scripts/run_daily.py --dry-run               # 수집 없이 리포트만

# 4) 주간 회고 (금요일 마감 후, 채점 5일 이상 쌓인 뒤)
python scripts/run_weekly.py
python scripts/run_weekly.py --next "다음 주 가설 문장"

# 5) 성과 리포트 (운영용, 발행하지 않음)
python scripts/run_analytics.py --days 30

# 6) 측정 설치 점검 (GA4 태그·canonical·sitemap). 절차: docs/SETUP_ANALYTICS.md
python scripts/check_site.py
python scripts/check_site.py --snippet        # 스킨에 붙일 코드 출력
```

## 세 가지 산출물

| 스크립트 | 주기 | 용도 | 발행 |
|---|---|---|---|
| `run_daily.py` | 매일 | 일간 시장 기록 | O |
| `link_post.py` | 발행 직후 | 글 번호 기록 (15초) | — |
| `run_weekly.py` | 주 1회 | **가설 검증 회고** | O |
| `run_analytics.py` | 주 1회 | 블로그 성과 분석 | X (운영 문서) |
| `check_site.py` | 스킨 수정 후 | 측정 태그·canonical·sitemap 점검 | X (운영 확인) |

주간 회고가 수익 측면에서 더 중요하다. 일간 글은 하루 지나면 죽지만
"뉴스 감성이 익일 수익률을 설명하는가" 같은 글은 계속 검색된다. 애드센스 수익은
롱테일에서 나온다. `run_analytics.py`가 이 가설을 실제 데이터로 검증해준다
(`weekly_vs_daily_ratio`).

## 3채널 구조

한 번 실행하면 세 채널 산출물이 동시에 나온다. 역할이 다르므로 하나를 고르는
문제가 아니다.

| 채널 | 역할 | 자동화 | 내용 |
|---|---|---|---|
| **GitHub** | 코드·데이터·아카이브 | **완전 자동** (git push) | 전문 + 데이터 |
| **티스토리** | 본진 (애드센스) | 붙여넣기 | 전문 |
| **네이버** | 유입 채널 | 붙여넣기 | 요약본 (원문의 ~38%) |

```
posts/2026-07-24.md          GitHub 아카이브 (Jekyll front matter 포함)
assets/2026-07-24/*.png
data/scorecard.json          누적 성적표

out/2026-07-24/
├── tistory/
│   ├── title.txt
│   ├── post.html            HTML 모드 붙여넣기용 (목차·요약박스·앵커 포함)
│   ├── tags.txt
│   ├── README.txt           게시 절차 + 체크리스트
│   └── images/              차트 4장
└── naver/
    ├── title.txt
    ├── post.txt             평문 요약본
    ├── tags.txt
    ├── README.txt
    └── images/              대표 2장만
```

### 왜 두 블로그 모두 붙여넣기인가

**둘 다 API가 죽었다.**

- 네이버 글쓰기 API: **2020년 5월 6일 종료.** 사유가 정확히 "광고성 대량 생산
  포스팅 방지"였다.
- 티스토리 Open API: **2024년 2월 완전 종료.** 파일첨부 → 글 → 댓글 순으로 순차
  종료되었고 유지보수도 중단됐다.

브라우저 자동화로 우회하면 ToS 위반 소지에 더해 저품질 문서로 분류될 위험이 크다.
그래서 **붙여넣기 직전까지만** 자동화한다. 실제 수동 작업은 하루 5분이다.

### 매일 루틴 (5분)

1. Actions 아티팩트 다운로드 (또는 로컬 실행)
2. `tistory/post.html` → 티스토리 HTML 모드 붙여넣기 → **2번 블록 한두 문장 손보기**
   → 이미지 4장 드래그 → 07:00 예약 → 발행
3. **발행된 글 번호를 기록** (15초)
   ```bash
   python scripts/link_post.py https://dailyresidualnote.com/123
   ```
4. `naver/post.txt` → 네이버 붙여넣기 → 이미지 2장 → 07:10 예약
   (원문이 먼저 색인되도록 10분 뒤에 올린다)

2번 블록(귀인 서술)을 손보는 단계를 빼지 말 것. 매일 실제로 다르게 나오는 유일한
부분이고, 이 한 단계가 자동 생성 티를 지운다.

### 3번 단계가 왜 필요한가

티스토리 포스트 주소를 '숫자'로 두면 URL(`/123`)이 **발행하는 순간** 결정된다.
발행 전에는 알 수 없으므로 두 가지가 깨진다.

1. 네이버 요약본의 원문 링크 — 어디로 보낼지 모른다
2. 애널리틱스 — `/123`에서 날짜를 못 뽑아 성과 분석이 안 된다

'문자' 주소로 바꾸는 대안이 있으나 제목이 한글이라 URL이 퍼센트 인코딩 범벅이 된다.
그래서 숫자를 유지하고 `link_post.py`로 한 번 기록한다. 이게

- `posts/*.md`의 `canonical_url`
- 네이버 요약본의 원문 링크
- `data/post_urls.json` (애널리틱스가 경로→날짜 매핑에 사용)

를 한꺼번에 맞춘다. 멱등이라 여러 번 돌려도 안전하다.

```bash
python scripts/link_post.py --list                 # 기록 현황
python scripts/link_post.py 123                    # 숫자만 입력해도 됨
python scripts/link_post.py https://.../200 --kind weekly
```

기록을 빼먹으면 그 날 글이 성과 분석에서 누락된다. `run_analytics.py`가 매칭
실패 건수를 로그로 알려준다.

### 중복 콘텐츠 처리

네이버에 전문을 옮기지 않는다. 같은 글이 두 도메인에 있으면 검색엔진이 정본을
골라야 하고, 수익이 나는 쪽(티스토리)의 순위를 네이버 글이 잡아먹을 수 있다.
네이버는 요약 + 원문 링크 1개만 둔다. GitHub 아카이브의 front matter에도
`canonical_url`이 티스토리를 가리키도록 박힌다.

### 반드시 먼저 할 일

`config.yaml`의 `site_url`과 `repo_url`을 본인 것으로 교체한다.

**티스토리에 자체 도메인을 처음부터 연결할 것.** 플랫폼 정책 변경·서비스 리스크에
대한 유일한 보험이다. 도메인 없이 쌓은 SEO는 플랫폼에 묶여서 이사할 수 없다.

## 필요한 키

| 키 | 필수 | 발급 |
|---|---|---|
| `FRED_API_KEY` | 사실상 필수 | https://fredaccount.stlouisfed.org/apikeys (무료·즉시) |
| `ALPHAVANTAGE_API_KEY` | 강력 권장 | https://www.alphavantage.co/support/#api-key (무료·즉시) |
| `SEC_USER_AGENT` | EDGAR 쓸 때 | 형식: `프로젝트명 이메일` (미기재 시 403) |
| `ANTHROPIC_API_KEY` | 선택 | 없으면 룰베이스 폴백으로 전체 동작 |
| `GA4_PROPERTY_ID` 외 | 선택 | 없으면 CSV 폴백 |

**키가 하나도 없어도 파이프라인은 끝까지 돌아간다.** 의도한 설계다. 그래야 로직 버그와
API 문제를 분리해서 디버깅할 수 있다.

### Alpha Vantage를 권장하는 이유

RSS만 쓰면 **종목 태깅이 부정확하다.** 회사명 부분일치는 오탐이 많고, 티커 대문자
매칭은 일반 단어와 충돌한다(A, ALL, CAT, KEY, ON, IT). 이게 지금까지 이 파이프라인의
가장 큰 데이터 품질 병목이었다.

Alpha Vantage `NEWS_SENTIMENT`는 `ticker_sentiment` 배열에 종목별
**relevance_score**를 담아준다. 0.25 이상만 취하면 태깅 문제가 근본적으로 해결된다.
덤으로 토픽 라벨도 붙어 오므로 LLM 분류 호출이 줄어든다.

**단, 무료 티어가 하루 25요청 · 분당 5요청으로 빡빡하다.** 그래서 종목별로 부르지
않고 토픽 배치로 하루 4회만 호출하도록 설계했다(`limit=1000`이라 한 번에 많이 받는다).
AV 자체 감성 점수는 산출 방식이 비공개라 보조 지표로만 저장하고, 주 분석은 재현
가능한 Loughran-McDonald 사전 점수를 쓴다.

## 구조

```
src/
├── config.py             설정 + .env 로더
├── calendar_utils.py     거래일·DST·뉴스창 (look-ahead 차단)
├── storage.py            append-only parquet upsert
├── collect/
│   ├── prices.py         yfinance, 배당조정 수익률
│   ├── macro.py          FRED
│   ├── factors.py        Ken French + ETF 프록시 2단 구조
│   ├── news.py           RSS + SEC EDGAR + 종목 태깅
│   ├── news_alphavantage.py  relevance_score 기반 정밀 태깅
│   └── analytics.py      GA4 + AdSense (읽기 전용, CSV 폴백)
├── process/
│   ├── residual.py       FF5+UMD 롤링 베타, Vasicek 축소
│   ├── sentiment.py      Loughran-McDonald 사전, novelty
│   ├── attribution.py    섹터·팩터 분해, 토픽 Ridge, 스코어카드
│   └── weekly_stats.py   Newey-West t, 이항검정, HLZ 허들
├── llm/                  프로바이더 어댑터 (anthropic/openai/rule)
├── report/
│   ├── charts.py         일간 4종, 한글 폰트 자동 탐색
│   ├── builder.py        일간 5블록 마크다운
│   ├── weekly_charts.py  누적 곡선, 주간 막대, 롤링 적중률
│   ├── weekly_builder.py 가설-데이터-결과-판정-한계-다음가설 6단
│   └── analytics_report.py  성과 x 시장상황 교차분석
└── publish/
    ├── common.py         세 줄 요약, 태그
    ├── github_archive.py 완전 자동. front matter + scorecard.json
    ├── tistory_package.py 목차·요약박스·앵커 포함 HTML
    ├── naver_package.py   평문 요약본, 외부 링크 1개
    ├── url_registry.py    숫자형 포스트 주소 <-> 날짜 매핑
    └── site_audit.py      배포된 사이트 점검 (GA4 태그·canonical·sitemap)
```

측정 설치 절차(GA4, 서치콘솔, 사이트맵)는 [docs/SETUP_ANALYTICS.md](docs/SETUP_ANALYTICS.md).
티스토리 관리자 작업은 손으로 하고 **결과 검증만 자동화**했다 — GA4는 소급 수집이
없어서, 태그 누락을 늦게 알면 그 기간 트래픽이 영구히 사라진다.

## 주간 회고의 통계 설계

일간 글이 "무슨 일이 있었나"라면 주간 글은 "내 신호가 작동하는가"다.
과잉 주장을 막기 위해 세 가지를 강제한다.

- **Newey-West(HAC) t.** 일별 스프레드는 자기상관이 있다. OLS 표준오차를 쓰면
  t가 부풀려진다.
- **이항검정 p.** "적중률 60%"만 쓰면 표본이 5일인지 200일인지 알 수 없다.
- **|t| > 3.0 허들.** Harvey, Liu & Zhu(2016)는 다중검정을 고려하면 관행적 2.0
  기준으로 우연히 유의한 팩터가 대량 생산된다고 지적했다. 매주 여러 스펙을
  들여다보는 이 프로젝트는 정확히 그 함정에 취약하다.

판정은 4단계로만 나온다: `판단 보류` / `귀무가설 기각 실패` / `결정적이지 않음` /
`기각 실패 (신호 존재 가능)`. 표본이 20거래일 미만이면 무조건 판단 보류다.

## 성과 리포트가 답하는 질문

단순 조회수 순위가 아니다. 시장 데이터와 채점 기록을 갖고 있으므로 교차분석이 된다.

- **Q1.** 시장이 크게 움직인 날의 글이 더 읽히는가? (Spearman ρ)
- **Q2.** 신호가 적중한 날의 글이 더 읽히는가? (발행 후 재유입 여부)
- **Q3.** 주간 글이 일간 글보다 오래 읽히는가? (`weekly_vs_daily_ratio`)
- **Q4.** RPM 상위 글의 공통점은 무엇인가?

Q3이 핵심이다. "주간 글이 검색 가치가 높다"는 지금까지 가설이었고, 이 리포트가
그걸 처음으로 데이터로 확인한다. 비율이 1.3 미만으로 나오면 주간 글의 제목·키워드를
재검토해야 한다.

## 리포트 5블록

1. **팩트** — 지수·섹터·팩터·매크로. 숫자만, 형용사 없이.
2. **귀인** — 토픽 노출 Ridge 회귀. 인과 주장 금지.
3. **이례치** — ±2σ 이탈 종목과 매칭 뉴스. 설명 안 되면 "미설명"으로 남긴다.
4. **검증** — 어제 감성 신호 5분위 스프레드의 실현값. 틀린 날도 지우지 않는다.
5. **일정** — 다음 거래일 지표·실적.

`builder.BANNED`에 금지 표현 목록이 있다. "혼조세", "관망세", "주목된다" 같은
정보량 0인 표현을 출력 단계에서 검사해 경고한다.

## 시간 설계

GitHub Actions cron은 UTC만 받는다. 미국 서머타임 때문에 16:00 ET의 UTC 시각이
여름 20:00 / 겨울 21:00 로 1시간 움직인다. 그래서 **cron을 KST로 고정하지 않고**,
`calendar_utils.last_completed_session()`이 "직전 거래일의 마감이 실제로 끝났는가"를
판정한다. NYSE 휴장일은 `pandas_market_calendars`로 처리한다.

목표는 **07:00 KST 발행**. Actions 스케줄은 러너 혼잡 시 지연될 수 있으므로,
정각이 중요하면 네이버 예약 발행을 함께 쓸 것.

## 검증

`tests/test_pipeline.py`는 알려진 베타로 합성 패널을 만들어 잔차 추정이 그 베타를
되찾는지 확인한다. 외부망 없이 돈다.

```
시장베타 복원 MAE = 0.066
표준화 잔차 sd    = 1.12  (기대 ~1.0)
차트 4장, 마크다운/HTML 생성, 금지어 0건
```

## 면책

본 저장소의 산출물은 공개 데이터를 자동 수집·분석한 연구 기록이며 투자자문이 아니다.
