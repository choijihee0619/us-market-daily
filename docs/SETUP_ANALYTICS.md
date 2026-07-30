# 측정 설치 (GA4 + 서치콘솔 + 사이트맵)

`CLAUDE.md` 7·8번 항목의 실행 문서다. 소요 30~40분, 전부 1회성이다.

**왜 지금 하는가.** GA4는 **소급 수집이 없다.** 태그를 넣기 전의 트래픽은 영구히
사라진다. 애드센스 승인은 30~50편 뒤 일이지만, 승인 심사 때 "이 블로그의 독자가
어디서 오는가"를 답할 데이터는 지금부터만 쌓인다. 서치콘솔의 색인 요청도 마찬가지로
등록 시점 이후 데이터만 남는다. 첫 글을 올리기 전에 끝내는 것이 순서상 맞다.

**자동화 범위.** 티스토리 관리자 화면 작업은 손으로 한다(Open API 2024-02 종료,
브라우저 자동화는 `CLAUDE.md` 3장에서 금지). 대신 **결과 검증은 자동화했다.**

```bash
python scripts/check_site.py            # 실제 배포된 HTML을 받아 대조
python scripts/check_site.py --snippet  # 붙여넣을 코드 출력
```

손으로 한 설정은 "했다고 생각했는데 안 된" 상태로 조용히 남는다. 매 단계 뒤에
이 스크립트를 돌린다.

---

## 시작 시점 상태 (2026-07-30 실측)

```
[ OK ] home              https://dailyresidualnote.com 200
[FAIL] ga4_missing.desktop
[FAIL] ga4_missing.mobile
[ OK ] canonical         https://dailyresidualnote.com
[ OK ] robots            전체 차단 없음
[WARN] robots_no_sitemap 티스토리는 Sitemap 지시자를 넣지 않는다
[ OK ] sitemap           <loc> 5건, 전부 dailyresidualnote.com
[INFO] sitemap_no_posts  글 경로(/숫자) 없음 (발행 0편)
[INFO] rss_empty
```

읽어야 할 것:

- **canonical은 이미 정상이다.** 티스토리가 개인 도메인 기준으로 생성해준다.
  스킨에 손으로 넣을 필요가 없다.
- **`sitemap.xml`과 `/rss`가 이미 존재한다.** 직접 만들 필요 없다(아래 STEP 6).
- **`robots.txt`에 Sitemap 지시자가 없고 편집도 불가하다.** 그래서 서치콘솔 수동
  제출이 선택이 아니라 필수다.
- 남은 실제 작업은 **GA4 태그 삽입**과 **서치콘솔 등록** 두 개다.

---

## STEP 1. GA4 속성 만들기 — 값 두 개를 헷갈리지 말 것

<https://analytics.google.com> → 관리(⚙️) → 계정 만들기 → 속성 만들기 →
데이터 스트림 → 웹 → URL `https://dailyresidualnote.com`

여기서 서로 다른 값 두 개가 나온다. 이걸 섞으면 수집도 조회도 조용히 실패한다.

| 값 | 형태 | 어디서 보는가 | 어디에 쓰는가 |
|---|---|---|---|
| **측정 ID** | `G-XXXXXXXXXX` | 데이터 스트림 상세 우측 상단 | 티스토리 스킨의 gtag (STEP 2) |
| **속성 ID** | 숫자 9자리 | 관리 → 속성 설정 → 속성 세부정보 | `.env`의 `GA4_PROPERTY_ID` (STEP 4) |

측정 ID를 `config.yaml`에 적는다. 페이지 소스에 그대로 노출되는 공개 값이므로
`.env`가 아니다. 여기 적어두면 `check_site.py`가 이후 스킨 변경으로 태그가
바뀌거나 사라진 것을 잡아낸다.

```yaml
site:
  ga4_measurement_id: "G-XXXXXXXXXX"
```

**데이터 보관 기간을 14개월로 늘려둔다.** 관리 → 데이터 수집 및 수정 → 데이터
보관. 기본값 2개월이면 1년 뒤 연간 비교가 불가능하다. 되돌릴 수 없는 손실이라
지금 바꾸는 게 맞다.

---

## STEP 2. 티스토리 스킨에 태그 넣기 — 모바일 함정이 핵심

붙여넣을 코드를 스크립트가 만들어준다(측정 ID를 config에 적은 뒤 실행).

```bash
python scripts/check_site.py --snippet
```

티스토리 관리 → **스킨 편집** → **html 편집** → `</head>` **바로 앞**에 붙여넣고 적용.

### 모바일 웹 자동 연결을 끈다

실측(2026-07-30)에서 확인한 문제다. 모바일 UA로 요청하면 티스토리가 `/m/` 의
**별도 시스템 스킨**으로 보낸다(데스크톱 32KB vs 모바일 9KB, 완전히 다른 HTML).
스킨 편집으로 넣은 gtag는 그 페이지에서 **실행되지 않는다.** 국내 블로그 트래픽은
모바일 비중이 크므로, 이대로 두면 수치가 조용히 반토막 나고 그 손실은 소급 복구되지
않는다.

조치: 티스토리 관리 → 스킨(또는 꾸미기) → **모바일 웹 자동 연결 사용 안 함**으로
변경. 그러면 모든 요청이 반응형 데스크톱 스킨으로 가고 태그 하나로 전부 잡힌다.

현재 스킨의 `<head>`에 `<meta name="viewport" content="width=device-width...">`가
있어 반응형으로 보인다. 다만 실제 모바일 레이아웃이 깨지지 않는지는 끄고 나서
휴대폰으로 한 번 봐야 안다. `[검증 필요]`
(레이아웃이 깨진다면 차선책은 반응형 스킨으로 교체다. `/m/`에 태그를 넣는 경로는
없다.)

메뉴 라벨은 티스토리 UI 개편에 따라 달라질 수 있다. `[검증 필요]`

### 플러그인 방식을 쓰지 않는 이유

티스토리 관리 → 플러그인에 구글 애널리틱스 항목이 있으나, GA4 측정 ID(`G-`)를
받는지 옛 UA 추적 ID(`UA-`)만 받는지 확인되지 않았다. `[검증 필요]`
스킨 직접 삽입은 결과가 페이지 소스에 그대로 보이고 `check_site.py`로 검증되므로
불확실성이 없다. 이쪽을 기본으로 한다.

### 검증

```bash
python scripts/check_site.py
```

`ga4_missing.desktop`과 `ga4_missing.mobile`이 둘 다 사라져야 한다. 데스크톱만
OK이고 `mobile_skin_split` 경고가 뜨면 모바일 웹 자동 연결이 아직 켜져 있다는 뜻이다.

추가로 GA4 → 보고서 → 실시간에서 본인 방문이 잡히는지 확인한다. 스크립트는 태그가
페이지에 있는지만 보고, 데이터가 실제로 GA4에 도착하는지는 실시간 보고서만 답한다.
둘 다 봐야 설치가 끝난 것이다.

---

## STEP 3. (선택) GA4 Data API — CSV 폴백을 대체

`run_analytics.py`는 키가 없으면 `data/analytics/traffic.csv` 폴백으로 돈다.
매주 CSV를 내려받는 게 귀찮아질 때 이걸 한다. 급하지 않다.

1. Google Cloud Console → 프로젝트 생성 → **Google Analytics Data API** 사용 설정
2. 서비스 계정 생성 → 키(JSON) 생성 → 다운로드
3. **GA4 속성 → 관리 → 속성 액세스 관리 → 서비스 계정 이메일을 뷰어로 추가**
   (2번까지만 하고 이 단계를 빠뜨리면 권한 오류가 난다. 가장 흔한 실수다.)
4. `.env`:
   ```
   GA4_PROPERTY_ID=123456789
   GOOGLE_APPLICATION_CREDENTIALS=/절대경로/service-account.json
   ```
5. `pip install google-analytics-data` (requirements.txt에 주석으로 있다)

JSON 키 파일은 저장소 밖에 두거나 `.gitignore`를 확인한다.

애드센스는 서비스 계정을 지원하지 않아 사용자 OAuth가 필요하다(`CLAUDE.md` 8장 4번).
승인 전에는 할 일이 없으므로 나중으로 미룬다.

---

## STEP 4. 서치콘솔 등록 — 도메인 속성으로

<https://search.google.com/search-console> → 속성 추가

**"도메인" 속성**(`dailyresidualnote.com`)을 고른다. URL 접두어 속성이 아니다.
이유: 도메인 속성은 http/https·www 유무·서브도메인을 한 속성에 모은다. 반면 URL
접두어는 변형마다 따로 등록해야 하고, 어느 변형으로 색인되었는지에 따라 데이터가
쪼개진다.

확인 방법은 **DNS TXT**뿐이다(도메인 속성의 유일한 방식). 가비아:
My가비아 → DNS 관리툴 → `dailyresidualnote.com` → DNS 설정 → 레코드 추가

| 타입 | 호스트 | 값 |
|---|---|---|
| TXT | `@` | `google-site-verification=...` (서치콘솔이 준 문자열) |

기존 CNAME(`host.tistory.io.`)은 **건드리지 않는다.** TXT 레코드는 별개 레코드로
공존한다. 전파는 보통 수분, 최대 수십 분.

> 스킨 `<head>`에 메타태그를 넣는 방식(URL 접두어 속성)도 되지만 권장하지 않는다.
> 스킨을 교체하면 소유확인이 날아간다. 그래도 이 방식을 쓴다면 값을
> `config.yaml`의 `site.google_site_verification`에 적어두면 `check_site.py`가
> 태그 유실을 감시한다.

### 기본 주소도 별도 속성으로 등록해 둘 것

`heeppiness.tistory.com` — URL 접두어 속성으로 추가한다(티스토리 소유 도메인이라
DNS TXT를 넣을 수 없으므로 도메인 속성은 불가). 확인은 서치콘솔이 제안하는
대체 방식 중 가능한 것을 쓴다. 안 되면 넘어간다.

목적은 트래픽 획득이 아니라 **관찰**이다. 티스토리는 기본 주소를 리다이렉트하지
않는다. 개인 도메인이 아닌 주소가 검색에 노출되고 있는지 확인할 수 있는 유일한
창구다(`CLAUDE.md` 8장 5번 미해결 항목). 노출이 잡히면 그때 대응을 고민한다.

---

## STEP 5. 사이트맵 제출

티스토리가 이미 만들어 두었다. 실측으로 확인했다.

| 경로 | 상태 | 용도 |
|---|---|---|
| `https://dailyresidualnote.com/sitemap.xml` | 200, `<loc>` 5건 | 서치콘솔 제출 |
| `https://dailyresidualnote.com/rss` | 200 | 보조 제출, 네이버·피드리더 |

서치콘솔 → Sitemaps → `sitemap.xml` 입력 → 제출. 이어서 `rss`도 제출한다.
RSS는 최신 글만 담아 신규 발행 감지가 빠르다. 둘을 같이 두는 게 표준 관행이다.

`robots.txt`에 `Sitemap:` 지시자를 넣을 수 없으므로(티스토리 생성 파일, 편집 불가)
이 수동 제출이 크롤러에 사이트맵을 알리는 유일한 경로다.

**발행 0편이라도 지금 제출한다.** 사이트맵 처리 상태가 "성공"으로 잡히는 데 며칠
걸리고, 그 시계를 미리 돌려두는 편이 낫다.

---

## STEP 6. (선택) 네이버 서치어드바이저

네이버는 이 프로젝트의 유입 채널이다(`CLAUDE.md` 2장). 네이버 블로그 요약본과
별개로, 티스토리 본문도 네이버에 색인될 수 있다.

<https://searchadvisor.naver.com> → 웹마스터도구 → 사이트 등록 →
`https://dailyresidualnote.com` → HTML 태그 방식 → 값을 `config.yaml`의
`site.naver_site_verification`에 적고 `--snippet`으로 코드를 받아 스킨에 삽입 →
`check_site.py`로 검증 → 네이버에서 소유확인 → 사이트맵 `sitemap.xml` 제출.

메타태그 방식을 쓰는 이유: 네이버는 DNS 확인을 제공하지 않는다. 그래서 이쪽은
스킨에 넣는 것 말고 선택지가 없고, 스킨 교체 시 유실되므로 config에 값을 적어
`check_site.py`가 감시하게 한다.

---

## 발행 루틴에 추가되는 것

첫 글을 올린 다음:

```bash
python scripts/link_post.py https://dailyresidualnote.com/1   # 기존 절차
python scripts/check_site.py --latest-post                    # 추가
```

`--latest-post`는 레지스트리의 최신 글을 받아 **그 글 페이지에** gtag가 있는지,
canonical이 그 글 자신을 가리키는지 본다. 홈페이지만 통과하고 글 페이지에서
깨지는 경우가 있다(글 보기 스킨이 별도 템플릿인 경우). 매일 돌릴 필요는 없고,
스킨을 건드린 다음과 첫 발행 직후에 한 번씩 돌리면 된다.

---

## 하지 말 것

- **스킨에 JS 리다이렉트를 넣어 기본 주소를 개인 도메인으로 보내기.** 티스토리
  이용약관 위반이고 계정 정지 사유다. canonical로 끝낸다.
- **본문에 애드센스 코드 수동 삽입.** 자동광고로 충분하고 과다 삽입은 정책 위반
  소지가 있다.
- **GA4 태그를 두 군데 넣기**(스킨 + 플러그인). 이중 집계로 조회수가 부풀고,
  나중에 성과 분석(`run_analytics.py`)의 모든 수치가 틀어진다.
  `check_site.py`가 `ga4_duplicate`로 잡는다.

---

## 남은 불확실성

| 항목 | 상태 |
|---|---|
| 모바일 웹 끈 뒤 레이아웃이 멀쩡한가 | `[검증 필요]` — 휴대폰으로 직접 확인 |
| 티스토리 GA 플러그인이 `G-` ID를 받는가 | `[검증 필요]` — 스킨 삽입으로 우회 중 |
| 네이버가 canonical을 무시하는가 | 통제 불가, 서치콘솔 두 속성으로 관찰만 |
| 애드센스 서비스 계정 미지원 | `[검증 필요]` — 승인 후 확인 |
