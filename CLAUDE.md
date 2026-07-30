# CLAUDE.md

이 파일은 세션이 바뀌어도 맥락이 끊기지 않게 하기 위한 인수인계 문서다.
**작업 시작 전에 이 문서를 먼저 읽고, 이미 결정된 사항을 다시 논의하지 말 것.**

---

## 1. 프로젝트 정체성

미국 시장 하루를 **관측치 하나**로 기록하는 파이프라인 + 블로그.

핵심 포지셔닝: **예측을 팔지 않고 기록을 남긴다.** 매일 리포트 4번 블록에서
전날 신호를 공개 채점한다. 맞은 날과 틀린 날을 모두 그대로 둔다.

이 프레임이 세 가지를 동시에 해결한다.
1. 유사투자자문 규제 리스크를 낮춘다 (전망·권유를 하지 않으므로)
2. 애드센스가 요구하는 원본성·human oversight를 충족한다
3. 실시간 out-of-sample 기록이 쌓여 박사 연구 자산이 된다

운영자는 디지털금융·핀테크·블록체인 전공 박사과정생이다. 코드는 Python(pandas 등
분석/ML) 기준. 통계·계량 논의는 근거와 함께 꼼꼼히, 코드는 필요한 부분만 짧게.

---

## 2. 이미 결정된 사항 (재논의 금지)

### 채널 3개, 역할 분리

| 채널 | 역할 | 자동화 | 이유 |
|---|---|---|---|
| GitHub | 코드·데이터·아카이브 | **완전 자동** | 수익원이 아니라 크리덴셜 자산 |
| 티스토리 | 본진 (애드센스) | 붙여넣기 | 애드센스를 붙일 수 있는 유일한 실질 선택지 |
| 네이버 | 유입 채널 (요약본만) | 붙여넣기 | 국내 검색 노출은 압도적이지만 애드포스트 RPM이 바닥 |

**두 블로그 모두 API가 죽었다.** 네이버 글쓰기 API는 2020-05-06 종료(사유가
"광고성 대량 생산 포스팅 방지"), 티스토리 Open API는 2024년 2월 완전 종료.
그래서 붙여넣기 직전까지만 자동화한다. 실제 수동 작업 5분.

### 도메인

`https://dailyresidualnote.com` — 가비아 구입, CNAME `host.tistory.io.` (끝점 필수),
티스토리 개인 도메인 연결 완료, SSL 발급 완료.

**티스토리는 자동 리다이렉트를 하지 않는다.** canonical 태그만 넣는다.
`heeppiness.tistory.com`도 계속 접속되지만 모든 링크·서치콘솔·사이트맵은
개인 도메인 기준으로 통일한다.

### 포스트 주소는 숫자형

티스토리 '포스트 주소' 설정이 `숫자`라서 URL(`/123`)이 발행 시점에 결정된다.
문자 주소로 바꾸면 한글 제목이 퍼센트 인코딩 범벅이 되므로 숫자를 유지한다.

대응: 발행 후 `scripts/link_post.py`로 한 번 기록하면 canonical, 네이버 링크,
애널리틱스 경로 매핑이 한꺼번에 맞춰진다. `config.yaml`의 `post_url_mode: numeric`.

### 위험모형은 FF5 + Carhart UMD

**예측 모형이 아니라 귀인 모형이다.** FF5는 수익률을 예측하지 않는다. 알려진 위험
노출로 설명되는 부분을 걷어내 잔차 `e_it`를 남기는 도구다. 뉴스가 설명하는 건 그 잔차다.

모멘텀을 더하는 이유: FF5에 모멘텀이 없어서 일간 잔차에 모멘텀 노출이 남고 그걸
뉴스 효과로 오인하게 된다. HML은 Fama-French(2015) 본인들이 미국 데이터에서 상당 부분
redundant라고 보고했으므로 계수 해석을 보수적으로 한다.

### 분석 대상 ≠ 신호 입력

- **분석 유니버스**: S&P 500 개별종목, 11개 GICS 섹터 ETF, 스타일 ETF
- **신호 입력**: VIX, UST 2y/10y, HY OAS, DXY, BTC 등 — 종속변수로 쓰지 않는다
- 선물·옵션·크립토를 같은 회귀에 넣으면 팩터 프레임워크가 무너진다

### 뉴스는 Alpha Vantage 우선

RSS 태깅이 부정확한 게 최대 병목이었다(회사명 부분일치 오탐, 티커-일반단어 충돌:
A, ALL, CAT, KEY, ON, IT). AV `NEWS_SENTIMENT`의 `ticker_sentiment.relevance_score`가
이걸 해결한다. **무료 티어 25요청/일·5요청/분**이라 토픽 배치로 하루 4회만 호출한다.

AV 자체 감성 점수는 산출 방식이 비공개라 보조 지표로만 저장하고, 주 분석은 재현
가능한 Loughran-McDonald 사전을 쓴다.

---

## 3. 절대 하지 말 것

- **브라우저 자동화로 블로그 자동 발행.** 기술적으로 가능하지만 (a) 두 플랫폼이
  API를 죽인 이유가 정확히 자동 대량 포스팅 방지, (b) 애드센스 scaled content abuse
  정책은 human oversight를 요구하고 계정 정지는 **계정 단위**라 복구가 어렵다.
  5분 아끼려고 수익 기반 전체를 걸지 않는다. **매일 사람이 2번 블록을 손보는 행위가
  정책이 요구하는 human oversight의 증거다.**
- **티스토리 스킨에 JS 리다이렉트 삽입.** 이용약관 위반, 계정 정지 사유.
- **네이버에 전문 복사.** 중복 콘텐츠로 티스토리(수익원) 순위를 잡아먹는다.
  요약 + 외부 링크 1개만.
- **본문에 수동 애드센스 코드 추가.** 자동광고로 충분하고, 과다 삽입은 정책 위반 소지.
- **개별 종목 매수·매도 시사.** 유료화 시 유사투자자문업 신고 대상이 될 수 있다.
  팩터·매크로 귀인 수준에 머문다.
- **금지 표현.** `builder.BANNED` 목록 참조 (혼조세, 관망세, 주목된다 등 정보량 0인 표현).

---

## 4. 현재 상태 (2026-07-30)

| 항목 | 상태 |
|---|---|
| 파이프라인 | 완료. 테스트 5종 통과 |
| 도메인 + SSL | 완료. canonical·sitemap.xml·rss 실측 정상 |
| `.env` — FRED, Alpha Vantage, SEC_USER_AGENT, OPENAI_API_KEY | 설정됨 |
| `.env` — GA4, AdSense OAuth | 미설정 (CSV 폴백으로 동작) |
| git 저장소 | **아직 init 안 됨** |
| `config.yaml` 의 `repo_url` | `USER/` placeholder — 교체 필요 |
| 과거 데이터 적재 | 아직 안 함 (`--backfill 500` 필요) |
| 첫 실제 실행 | 아직 안 함 |
| 발행한 글 | 0편 |
| GA4 태그 | **미설치** (데스크톱·모바일 둘 다 없음, 실측 확인) |
| 서치콘솔 | 미등록 |
| 측정 점검 도구 | 완료 (`scripts/check_site.py`, `docs/SETUP_ANALYTICS.md`) |

측정 관련 작업은 티스토리 관리자 화면에서 손으로 해야 한다(브라우저 자동화 금지,
3장 참조). 대신 **검증은 자동화했다** — `python scripts/check_site.py`가 실제 배포된
HTML을 받아 GA4 태그·canonical·소유확인 메타·sitemap 호스트를 대조하고, FAIL이 있으면
종료코드 1을 낸다. GA4는 소급 수집이 없어서 태그 누락을 늦게 알면 그 기간 트래픽이
영구히 사라진다. 그래서 "설치했다는 믿음"이 아니라 배포된 HTML로 확인한다.

`llm.provider`가 `rule`로 되어 있다. `OPENAI_API_KEY`가 있으므로 `openai`로 바꿀 수
있으나, **먼저 룰베이스로 파이프라인을 검증한 뒤** 바꿀 것. 로직 버그와 API 문제를
분리해서 디버깅하기 위한 설계다.

---

## 5. 구조

```
scripts/
  run_daily.py      매일. 수집 -> 잔차 -> 리포트 -> 3채널 산출물
  link_post.py      발행 직후. 글 번호 기록 (15초)
  run_weekly.py     주 1회. 가설 검증 회고
  run_analytics.py  주 1회. 성과 분석 (운영용, 발행 안 함)
  check_site.py     스킨 수정 후. 배포된 HTML로 측정 설치 검증

src/
  calendar_utils.py  거래일·DST·뉴스창. look-ahead 차단의 핵심
  storage.py         append-only parquet upsert
  collect/           prices, macro, factors, news, news_alphavantage, analytics
  process/           residual, sentiment, attribution, weekly_stats
  llm/               프로바이더 어댑터 (anthropic/openai/rule)
  report/            charts, builder, weekly_*, analytics_report
  publish/           github_archive, tistory_package, naver_package, url_registry,
                     site_audit (배포된 사이트 점검)

docs/MODELS.md            사용 모형 전체 목록 (7개 층위, 1단계/2단계 구분)
docs/SETUP_ANALYTICS.md   GA4·서치콘솔·사이트맵 설치 절차 (7·8번 항목 실행 문서)
```

### 리포트 5블록

1 팩트 (숫자만) · 2 귀인 (인과 주장 금지) · 3 이례치 (설명 안 되면 "미설명") ·
**4 검증 (이 프로젝트의 존재 이유)** · 5 일정

### look-ahead 차단 두 지점

1. 뉴스 창을 `[전일 16:00 ET, 당일 16:00 ET)`로 자른다 (`calendar_utils.news_window`)
2. 베타 추정창에서 당일을 배제한다 (`f.index < session`)

당일을 포함하면 그날 뉴스 충격이 베타에 흡수되어 잔차가 축소된다.

---

## 6. 코드 규약

- 주석·문서는 한국어. **"왜 이렇게 했는가"를 적는다.** 무엇을 하는지는 코드가 말한다.
- 검증하지 않은 추정치·경험칙은 `[검증 필요]`로 표시한다.
- 통계 결과를 보고할 때 표준오차·표본크기 없이 유의성을 주장하지 않는다.
- 새 기능은 `tests/`에 합성 데이터 검증을 함께 추가한다. **외부망 없이 돌아야 한다.**
  (알려진 정답을 심고 되찾는 방식. 예: `test_pipeline.py`가 심은 베타를 MAE 0.066으로 복원)
- 키가 없어도 파이프라인이 끝까지 돌아야 한다 (룰베이스·CSV 폴백).

---

## 7. 다음 할 일

### 즉시

1. `git init` → 첫 커밋 → GitHub 레포 생성 → `config.yaml`의 `repo_url` 교체
2. `python scripts/run_daily.py --backfill 500` (10~20분. yfinance가 일부 종목 실패할 수 있음)
3. `python scripts/run_daily.py` 첫 실제 실행
4. **AV `relevance_min` 0.25가 적정한지 실측 조정** ← 지금 가장 불확실한 값.
   내가 추정한 기본값이고 실제 데이터를 봐야 안다.
5. 뉴스 커버리지 확인: 이례치 종목 중 "미설명" 비율이 얼마나 되는지.
   무료 소스는 대형주 편향이 심하다. 이게 데이터 한계인지 실제 발견인지 구분하는 게
   2단계 진입 전 마지막 관문이다.

### 2~4주차

6. 매일 발행 (5분 루틴). 채점 5일 쌓이면 첫 주간 회고 가능
7. **GA4 설치** — 문서·검증 도구 준비 완료, **관리자 화면 작업만 남음.**
   절차: `docs/SETUP_ANALYTICS.md` STEP 1~2. 요약:
   - 측정 ID(`G-`)를 `config.yaml` 의 `site.ga4_measurement_id` 에 기입
     (`.env` 의 `GA4_PROPERTY_ID`(숫자)와 다른 값이다. 섞으면 조용히 실패한다)
   - `python scripts/check_site.py --snippet` 출력을 스킨 `</head>` 앞에 붙여넣기
   - **티스토리 '모바일 웹 자동 연결'을 끈다.** 켜져 있으면 모바일 요청이 `/m/` 의
     별도 시스템 스킨으로 가고 스킨에 넣은 gtag가 실행되지 않는다. 실측으로 확인했다
     (데스크톱 32KB vs 모바일 9KB, 완전히 다른 HTML). 국내 트래픽은 모바일 비중이
     커서 이걸 놓치면 수치가 조용히 반토막 난다
   - `python scripts/check_site.py` 로 FAIL 0 확인 + GA4 실시간 보고서 확인
   - GA4 데이터 보관 기간을 14개월로 변경 (기본 2개월은 연간 비교 불가)
8. **서치콘솔 등록** — 절차: `docs/SETUP_ANALYTICS.md` STEP 4~5. 요약:
   - **도메인 속성**으로 등록하고 가비아에 DNS TXT 추가 (기존 CNAME과 공존한다).
     URL 접두어 속성은 변형마다 데이터가 쪼개진다
   - `heeppiness.tistory.com` 도 URL 접두어 속성으로 따로 등록 — 8장 5번(canonical
     무시) 관찰 창구
   - `sitemap.xml` 과 `rss` 를 제출한다. 둘 다 티스토리가 이미 만들어 두었고 실측
     확인했다. `robots.txt` 에 `Sitemap:` 지시자가 없고 편집도 불가하므로 수동 제출이
     선택이 아니라 필수다

### 30~50편 시점

9. 애드센스 신청. 금융은 E-E-A-T 심사가 빡빡하니 방법론·필자소개 페이지를 고정 메뉴에
10. `run_analytics.py`로 `weekly_vs_daily_ratio` 확인.
    1.3 미만이면 주간 글 제목·키워드 재검토

### 2단계 (누적 60거래일 이후)

11. **(a) 뉴스 감성 → 익일 초과수익률**: Fama-MacBeth 2-pass + Newey-West(lag 5).
    통제변수에 log(size), B/M, 전일수익률(reversal), 기사수(attention), novelty.
    pooled OLS는 일자별 잔차 상관으로 t가 3~4배 부풀려지므로 쓰지 않는다.
12. **(b) Event study**: 추정창 [-250,-30], 사건창 [-1,+1]/[0,+5].
    개별기업 이벤트는 BMP standardized cross-sectional test.
    **매크로 이벤트는 event-date clustering이 치명적** — 전 종목이 같은 날 같은 사건을
    겪어 횡단면 잔차가 강하게 상관된다. Kolari-Pynnönen 보정 또는 calendar-time portfolio.
13. **(c) 토픽 회귀 유의성**: 현재는 Ridge R²만 보고한다. 잔차가 추정된 베타에서 나온
    generated regressand라 2단계 추정오차가 표준오차에 반영되지 않는다.
    Shanken 보정 또는 block bootstrap(20일 블록) 필요.
14. **다중검정 보정**: Harvey-Liu-Zhu |t|>3.0 허들(이미 적용), Deflated Sharpe Ratio,
    Benjamini-Hochberg FDR.
15. 뉴스레터 (스티비 무료 티어). **광고보다 이쪽이 본선이다** — 독자 500명 기준
    애드센스 월 8~20만원 vs 유료 뉴스레터 월 30~50만원.

---

## 8. 알려진 미해결 항목

1. **생존편향** — 현재 지수 구성종목을 쓴다. `snapshot_date`로 매일 저장 중이므로
   시간이 지나면 시점별 스냅샷이 쌓인다. 백테스트는 그걸 써야 한다.
2. **Ken French 팩터 지연** — 일간 파일이 수 주 지연된다. 당일은 ETF 프록시로 근사하고
   확정치가 오면 upsert로 소급 교체한다. **프록시는 서술용이고 회귀 계수 추정에는
   확정 팩터를 써야 한다.** 프록시-실제 상관 0.7~0.9로 알려져 있으나 `[검증 필요]`.
3. **FRED vintage 부재** — 잠정치가 확정치로 덮인다. "발행 시점에 무엇을 보고 있었나"가
   중요한 연구에는 ALFRED vintage가 필요하다.
4. **AdSense 서비스 계정 미지원** — 개인 Google 계정에 묶여 있어 OAuth 사용자 동의가
   필요하다. GA4는 서비스 계정으로 된다. `[검증 필요]`
5. **네이버 canonical 무시 사례** — 네이버가 canonical을 무시하고 tistory.com 주소를
   노출하는 보고가 있다. 통제 불가, 관찰만.
6. **일간 빈도의 한계** — 장중 반응은 분 단위인데 일간 종가로 보면 상당 부분 씻긴다.
   intraday로 내려가면 마이크로구조 노이즈가 새 문제로 등장한다.
