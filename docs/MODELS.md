# 사용 모델 전체 목록

이 문서는 블로그 포스팅 생산에 관여하는 모든 모형을 층위별로 정리한다.

> **중요한 구분**
> 여기 나오는 모형의 대부분은 **예측 모형이 아니라 귀인(attribution) 모형**이다.
> Fama-French는 수익률을 예측하지 않는다. 알려진 위험 노출로 설명되는 부분을 걷어내
> "설명되지 않는 움직임"을 남기는 도구다. 실제로 예측을 시도하는 건 L5 하나뿐이고,
> 그마저도 1단계에서는 계산하지 않는다. 블로그를 "예측 서비스"로 포지셔닝하지 않는
> 이유이기도 하다 — 유사투자자문 리스크와 직결된다.

구현 상태 표기: **[1단계]** 지금 돌아감 · **[2단계]** 설계됨, 미구현 · **[선택]** 여력 될 때

---

## L1. 위험모형 (잔차 생성)

기본 스펙 `ff5_umd`. 구현: `src/process/residual.py`

| 모형 | 식 | 상태 |
|---|---|---|
| CAPM | rᵢ−r_f = αᵢ + βᵢ·MKT + eᵢ | [1단계] 옵션 |
| Fama-French 3 | + SMB, HML | [1단계] 옵션 |
| Fama-French 5 | + RMW, CMA | [1단계] 옵션 |
| **FF5 + Carhart UMD** | + 모멘텀 | **[1단계] 기본** |
| q-factor (Hou-Xue-Zhang) | ME, I/A, ROE | [선택] |
| Barra 스타일 (산업 더미 포함) | 산업 + 스타일 | [선택] |

$$r_{i,t} - r_{f,t} = \alpha_i + \beta_i^{MKT}MKT_t + \beta_i^{SMB}SMB_t + \beta_i^{HML}HML_t + \beta_i^{RMW}RMW_t + \beta_i^{CMA}CMA_t + \beta_i^{UMD}UMD_t + e_{i,t}$$

**왜 FF5에 모멘텀을 더하는가.** FF5에는 모멘텀이 없다. 그대로 쓰면 일간 잔차에 모멘텀
노출이 남고, 그걸 뉴스 효과로 오인하게 된다. Carhart(1997) UMD를 추가해 제거한다.

**HML 해석 주의.** Fama & French(2015)는 FF5에서 HML이 상당 부분 다른 팩터의
조합으로 흡수되어(redundant) 미국 데이터에서 설명력이 거의 없다고 직접 보고했다.
HML 계수는 보수적으로 해석하고 단독 서술 근거로 쓰지 않는다.

---

## L2. 베타 추정 (L1의 입력)

| 방법 | 내용 | 상태 |
|---|---|---|
| Rolling OLS | 250거래일, 최소 120관측 | [1단계] |
| **Vasicek(1973) 축소** | 추정오차 큰 베타를 횡단면 평균으로 당김 | **[1단계] 기본** |
| Blume(1971) 조정 | β_adj = 0.67β + 0.33 | [1단계] 옵션 |
| Dimson(1979) 보정 | 비동시거래 보정. 대형주 위주라 영향 작음 | [선택] |
| DCC-GARCH / EWMA 베타 | 시변 베타 | [선택] |

Vasicek 가중: $w = \sigma^2_{cross} / (\sigma^2_{cross} + \sigma^2_{\hat\beta_i})$, $\beta_{adj} = w\hat\beta_i + (1-w)\bar\beta$

**추정창에 당일을 넣지 않는다.** 당일을 포함하면 그날의 뉴스 충격이 베타에 흡수되어
잔차가 인위적으로 축소된다. 코드에서 `f.index < session`으로 강제한다.

---

## L3. 텍스트 모형 (뉴스 → 수치)

구현: `src/process/sentiment.py`, `src/llm/`

| 모형 | 역할 | 상태 |
|---|---|---|
| **Loughran-McDonald 금융 사전** | 감성 baseline | **[1단계] 기본** |
| 4-gram shingle Jaccard | novelty(재탕 탐지) | [1단계] |
| 부정어 극성 반전 | "not strong" 처리 | [1단계] |
| 키워드 룰 분류기 | 토픽 12종, 무비용 baseline | [1단계] |
| LLM 다중라벨 분류 | 토픽 12종, 정밀도 향상 | [1단계] 어댑터 |
| FinBERT (ProsusAI) | 트랜스포머 감성 | [2단계] |
| Sentence-BERT 코사인 | novelty 고도화 | [2단계] |
| LDA / BERTopic | 비지도 토픽 발견 | [선택] |
| 3종 앙상블 (사전+FinBERT+LLM) | 불일치를 불확실성 신호로 | [2단계] |

**왜 범용 감성사전을 쓰면 안 되는가.** Loughran & McDonald(2011)는 Harvard IV
'부정' 단어의 약 4분의 3이 금융 문맥에서 부정이 아님을 보였다. liability, tax, cost,
capital, depreciation, vice(=vice president)가 전부 부정으로 잡힌다. 금융 텍스트에는
LM 사전을 쓴다.

**tone 정규화.** $tone = (n_{pos} - n_{neg}) / \sqrt{n_{tokens}}$. 단순 $/n$ 이 아니라
$\sqrt{n}$ 으로 나눈다. 헤드라인이 8~15 토큰으로 짧아 분모가 작고, 그대로 두면
극단값이 과하게 나온다.

**novelty 가중.** 같은 사건의 재작성 기사가 감성 점수를 뻥튀기하는 문제가 있다.
`novelty = 1 − max Jaccard`, 임계 미만이면 가중치를 0.3배로 낮춘다.

---

## L4. 귀인 모형 (잔차 ← 토픽)

구현: `src/process/attribution.py`

$$e_{i,t} = \delta_0 + \sum_k \delta_k \cdot \text{TopicExposure}_{k,i,t} + u_{i,t}$$

| 항목 | 선택 | 이유 |
|---|---|---|
| 추정량 | **Ridge (α=1.0)** | 토픽 열이 직교하지 않음. OLS는 부호가 표본마다 뒤집힘 |
| 대안 | Elastic Net | 토픽 수를 늘릴 때 |
| 보고 | R², 계수(bp)만 | **표준오차·유의성은 보고하지 않음** |

**왜 유의성을 주장하지 않는가.** 종속변수 $e_{i,t}$가 추정된 베타에서 나온
generated regressand라 2단계 추정오차가 표준오차에 반영되지 않는다. 제대로 하려면
Shanken 보정이나 block bootstrap이 필요하다. 1단계에서는 방향성 참고용으로만 쓰고,
그 사실을 본문에 명시한다.

---

## L5. 예측·검정 모형 **[2단계]**

여기부터가 실제 연구 기여 지점이다. 1단계 파이프라인이 데이터를 쌓는 동안 설계한다.

### (a) 뉴스 감성 → 익일 초과수익률

| 요소 | 선택 |
|---|---|
| 추정량 | **Fama-MacBeth 2-pass** (일별 횡단면 → 계수 시계열 평균) |
| 표준오차 | Newey-West (lag 5) |
| 통제변수 | log(size), B/M, 전일수익률(reversal), 기사수(attention), novelty |
| 대안 | 이원 클러스터 패널 FE (Petersen 2009) |

pooled OLS를 쓰면 일자별 잔차 상관 때문에 t값이 3~4배 부풀려진다.

### (b) Event study

| 요소 | 선택 |
|---|---|
| 추정창 / 사건창 | [−250, −30] / [−1, +1], [0, +5] |
| 개별기업 이벤트 | **BMP standardized cross-sectional test** |
| 매크로 이벤트 | **Kolari-Pynnönen 상관보정** 또는 calendar-time portfolio |

**event-date clustering이 핵심 함정.** FOMC·CPI는 전 종목이 같은 날 같은 사건을
겪으므로 횡단면 잔차가 강하게 상관된다. 표준 t-test는 귀무가설을 과도하게 기각한다.

### (c) 비선형 예측

| 모형 | 용도 |
|---|---|
| LightGBM / XGBoost | 토픽 × 매크로 상호작용 |
| 교차검증 | **Purged walk-forward + embargo** (López de Prado) |
| 벤치마크 | 과거평균 (Goyal-Welch: 대부분의 예측변수가 이걸 못 이김) |
| 비교검정 | Clark-West (중첩모형), Diebold-Mariano |
| 지표 | OOS R² (Campbell-Thompson) |

### (d) 다중검정 보정

매일 12개 토픽 × 여러 스펙을 돌리면 우연한 유의성이 나온다. 반드시 보정한다.

- Harvey-Liu-Zhu(2016): 팩터 발견에 |t| > 3.0 요구
- Deflated Sharpe Ratio (Bailey & López de Prado)
- Benjamini-Hochberg FDR

---

## L6. 시장 국면 **[선택]**

| 모형 | 용도 |
|---|---|
| VIX 3분위 | 저·중·고변동성 국면 분할 |
| Markov regime-switching | 국면 확률 추정 |
| Surprise index $(actual − consensus)/\sigma$ | 지표 서프라이즈 표준화 |

---

## L7. 성과 평가 (4번 블록)

구현: `attribution.scorecard()`

| 지표 | 내용 |
|---|---|
| 5분위 롱숏 스프레드 | 최상위−최하위 분위 잔차 차이 (bp) |
| 방향 적중률 | 스프레드 > 0 인 날의 비율 |
| 누적 t-통계량 | 일별 스프레드 시계열의 t |
| [2단계] Sharpe, MDD | 거래비용 차감 후 |

동일가중, 거래비용 미반영. **집행 가능한 전략 수익률이 아니라 신호의 방향성 기록**임을
본문에 매번 명시한다.

---

## 데이터 소스별 모형 의존성

| 소스 | 제공 | 지연 | 대체 |
|---|---|---|---|
| Yahoo Finance (yfinance) | 가격, 수익률 | 실시간~15분 | Polygon, Alpha Vantage |
| FRED | 금리, 크레딧, 달러, VIX | 1영업일 | Treasury FiscalData |
| Ken French Data Library | 확정 FF5+UMD | **수 주 지연** | ETF 프록시 (당일용) |
| SEC EDGAR | 8-K/10-Q/10-K | 실시간 | — |
| 무료 RSS | 헤드라인 | 실시간 | Finnhub, Marketaux |

**French 지연 대응.** 당일 리포트는 ETF 롱숏 스프레드 프록시로 근사하고, French
데이터가 들어오면 같은 날짜를 upsert로 소급 교체한다. 프록시와 실제 팩터의 상관은
0.7~0.9 수준으로 알려져 있으나 [검증 필요], **회귀 계수 추정에는 반드시 확정 팩터를
써야 한다.** 프록시는 서술용이다.

---

## 알려진 한계

1. **생존편향** — 현재 S&P 500 구성종목을 쓴다. 1단계 기술통계에는 문제없지만
   백테스트로 넘어갈 때는 시점별 스냅샷이 필요하다. `snapshot_date`로 매일 저장 중.
2. **종목 태깅 정밀도** — 회사명 부분일치는 오탐, 티커 매칭은 일반 단어와 충돌
   (A, ALL, CAT, KEY, ON). 3글자 이상 + 단어경계로 완화했으나 완전하지 않다.
   정밀 태깅은 유료 API의 entity 태그로 승격해야 한다.
3. **뉴스 커버리지** — 무료 RSS는 대형주 편향이 심하다. 중소형주 이례치는
   "미설명"으로 남는 경우가 많고, 이는 데이터 한계지 발견이 아니다.
4. **vintage 부재** — FRED 잠정치가 확정치로 덮인다. "발행 시점에 무엇을 보고
   있었는가"가 중요한 연구에는 ALFRED vintage가 필요하다.
5. **일간 빈도의 한계** — 장중 반응은 분 단위인데 일간 종가로 보면 상당 부분이
   씻긴다. intraday로 내려가면 마이크로구조 노이즈가 새 문제로 등장한다.

---

## 참고문헌

- Fama, E. F., & French, K. R. (2015). A five-factor asset pricing model. *JFE*, 116(1).
- Carhart, M. M. (1997). On persistence in mutual fund performance. *JF*, 52(1).
- Loughran, T., & McDonald, B. (2011). When is a liability not a liability? *JF*, 66(1).
- Fama, E. F., & MacBeth, J. D. (1973). Risk, return, and equilibrium. *JPE*, 81(3).
- Vasicek, O. (1973). A note on using cross-sectional information in Bayesian estimation of security betas. *JF*, 28(5).
- Boehmer, Musumeci, & Poulsen (1991). Event-study methodology under conditions of event-induced variance. *JFE*, 30(2).
- Kolari, J. W., & Pynnönen, S. (2010). Event study testing with cross-sectional correlation. *RFS*, 23(11).
- Petersen, M. A. (2009). Estimating standard errors in finance panel data sets. *RFS*, 22(1).
- Goyal, A., & Welch, I. (2008). A comprehensive look at the empirical performance of equity premium prediction. *RFS*, 21(4).
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). ...and the cross-section of expected returns. *RFS*, 29(1).
- Tetlock, P. C. (2007). Giving content to investor sentiment. *JF*, 62(3).
