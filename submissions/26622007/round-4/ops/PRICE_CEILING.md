# 가게 에이전트의 손익분기 조회

`price_ceiling`은 **계산만** 한다. `max_buy_cost`와 `min_sell_price`는 기본 가정의 개당 정수 크레딧, `expected_profit`은 입력 수량 전체의 예상 현금손익이다. `conservative_*`는 보수 시나리오다. `null`은 가격이 없다는 뜻이다. `buy`/`sell_ok`도 실현손익을 보장하지 않는다. 가게의 매입 중지나 예산·무보증 비식품 금지 정책을 해제하지 않는다.

## 키트 담당자 인계 (지금 등록하지 않음)

1. 다음 승인된 작업에서 가게 루트에 `ops/price_ceiling.py`와 `ops/price_ceiling_data.json`을 함께 배치한다. 런타임은 표준 라이브러리와 이 두 파일만 사용한다. 카탈로그·장부 집계는 매 호출 때 로컬 공개 캐시에서 읽으며 시장 요청은 **0회**다. 키트의 `root`가 가게 루트인 것을 먼저 확인한다.
2. `python3 -I ops/price_ceiling_forge.py`로 작은 `forge_tool` 요청을 확인하고 가게 에이전트가 해당 정의를 forge하게 한다. 템플릿은 `ops/forge_templates/price_ceiling/`의 `logic.py`, `test.py`, `permission.yml`이며 지갑·신원·네트워크·파일쓰기 권한이 모두 없다. `tools/`로 직접 복사하거나 `forge.json`을 만들지 않는다. 키트 `scripts/round4/agent_kit.py:284`는 키트 자신의 forge 기록과 일치하는 도구만 로드한다.
3. 매입·가격제시·판매 서명 **직전** 신뢰하는 원본 견적에서 인수를 다시 만들고 `price_guard(action, args)`를 호출한다. 일반 Python에서 `from ops.price_ceiling import price_guard`; `-I`로 도는 forged tool에서는 `from runpy import run_path; run_path('ops/price_ceiling.py')['price_guard'](action, args)`. `action`은 `buy`, `price`, `sell`. 오류·누락·`skip`·`too_low`이면 거래 전에 종료한다. 과거 조회 결과를 인수로 받거나 실패 시 통과시키지 않는다. 고객에게 이미 지는 환불 의무에는 이 매매 가드를 적용하지 않는다.

**goal 초안:** “매입·판매·가격 제시 전 `price_ceiling`으로 조회하고 실행 직전 원본 견적으로 `price_guard`를 다시 통과하라. `skip`·`too_low`·자료 누락·오류이면 진행하지 말고, 기존 매입 중지와 지갑 한도는 그대로 지켜라.”

## 입력

- `sku`, `sell`(실제/예상 개당 판매가, 정수) 또는 `cost`(개당 매입 원가). `action` 생략 시 `cost`만 주면 판매, 나머지는 매입 조회다. 두 값을 주고 판매 판정을 보려면 `action=sell`을 명시한다. 하나만 주는 탐색 조회는 반대 가격을 캐시에서 채운다. **가드는 두 가격과 `grade`, `hold_hours`, `lot_dues`(0도 명시), 식품의 경우 `remaining_hours`를 요구한다.**
- `qty`는 같은 조건의 총 수량, `batch_size`는 1회 매입 수량(최대 20), `lot_dues`는 이 수량에 배분할 기존 보관료·포럼비 합계(견적의 `storage + forum_fees`). `cost`에는 중복 산입하지 않는다. 혼합 lot은 등급·유통기한·가격이 다르므로 한 양호한 단위로 대표시키지 않는다. 가드 호출 전 같은 조건인지 확인하거나 개별 단위에 비용을 보수적으로 배분한다.
- `grade`는 비식품의 매입 등급, `hold_hours`는 매입부터 예상 판매까지 총시간. 식품 `remaining_hours`는 해당 계산의 시작점에서 확인한 잔여시간이며 `hold_hours`도 그 시점부터 센다. 가드에는 식품 잔여시간을 실제 재고/견적의 `expires_at`에서 구해 넣는다. `unsold`는 마감까지 미판매 확률(0~1). 매수 의향만으로 0을 주지 않는다. 등급이 낮아져 실제 흥정가가 바뀌면 `sell`도 갱신한다.
- 기본은 1개·1회·1시간·B. SKU별 실측 판매가(중앙값을 정수 내림), 원가(총 공급액/수량)를 사용한다. 없으면 초기 시장 비율인 정가의 60%·54%를 각각 사용하며 출처를 표시한다. 범위 밖/미등록 SKU는 거절한다.

## 공식: 가격과 지갑 현금을 혼동하지 않기

`P` 판매가, `C` 원가, `q` 수량, `b` 묶음, `h` 보유시간, `s` 판매 확률(기한 경과 시 0), `d` 판매 시 결함률, `D` 기존 lot 부대비용 합계:

```
F(P) = floor(P/100) + 210
R(P) = 0 if P < F(P) else s × [P − F(P) − d × (P + 206)]
K = (206 × ceil(q/b) + D) / q
a = 1 + 0.005 × h (비식품), 1 (식품)
E[전체 현금손익] = q × (R(P) − aC − K)
매입 상한 = floor((R(P) − K) / a), 음수면 없음
판매 하한 = R(P) ≥ aC + K 를 만족하는 최소 정수 P; s=0이면 없음
```

하한은 입력 가능한 범위(0~10¹²)에서 처음 손해가 아닌 가격이다. 비식품은 1% 수수료가 정수로 뛰는 경계에서 하한 바로 위 가격이 다시 소액 손실일 수 있으므로 부등식만 비교하지 말고 실제 제시가로 가드를 호출한다.

**확인** — [공식 Patch 4.0](https://market.ourolabs.ai/rules), [공개 시장 서버 62ad46bc](https://github.com/Q00/agent-market/blob/62ad46bc/market/server.py), 키트 `scripts/round4/maroo.py:83`:

- 소매 정산은 양측 각각 1% 내림 + 210. 가게 수입에서는 가게 몫만 빠지며 소매 가스는 구매자 부담(`server.py:934`, `:969`). 도매 매입은 `goods + storage + forum_fees`와 실제 가스다(`:1126`, `:1168`); 판매 수수료를 도매 lot에 덧붙이지 않는다. 같은 lot에서 나뉘는 고정비는 매입 가스와 lot 부대비용이며, 판매 1건당 수수료는 나누지 않는다.
- 비식품은 48시간마다 A→B→C, 매입~판매 합산 결함률은 3/12/35%(`:136`, `:265`, `:1002`). 식품에는 고장 확률이 없고 유통기한 이후 판매 불가(`:295`, `:982`, `:1114`). 마감 평가는 재고 평가액이 아닌 현금이다(과제 `weeks/week-12/README.md`, `server.py:2249`).
- 다음 매입 시점의 남은 비식품 재고 원가와 경과시간을 기준으로 정수 보관료가 붙는다(`:1186`, `:1193`). 미판매 재고의 회수액은 0이다.

**추정** — (1) 개별 품목에는 비식품 원가×0.5%×시간을 충당한다. 다음 매입 전에 팔거나 더 매입하지 않으면 실제 청구액은 다르다. (2) 우리 `logs/payments.jsonl`에는 확인 당시 grant만 있어 매입 가스의 자체 평균이 없다. 공개 거래 잔액 감소에서 관측한 약 205~206 중 206을 채택했다. 결함은 항상 정산가 전액 환불하고 송금 가스도 206으로 충당하는 가정이다. 시장이 강제 환불하는 것이 아니다. (3) 식품 유통기한을 주지 않은 **조회**는 공급 등급별 잔여시간 구간(`:135`) 중점을 사용한다. 보수 시나리오는 구간 하단과 `min(1, 미판매율 + 10%p)`를 쓴다. 이는 신뢰구간이 아니다. (4) 기본 미판매율은 표본 종류별 `1 − 판매 수량/공급 수량`이다. 초기 단기 판매속도의 대용값으로, 가게별 수요·재고 노출시간·실제 마감 확률을 측정한 것이 아니다. 정상 수요 근거가 있으면 `unsold`를 직접 지정한다. 부패 외 고객의 요구 유통기한 미충족, 가격/등급에 따른 수요 차이도 이 확률과 판매가에 반영해야 한다.

## 확인과 갱신

```
python3 -I ops/price_ceiling.py --sku G00b8acf0 --sell 3102 --qty 3 --batch-size 3 --hold-hours 2 --json
python3 -I ops/price_ceiling.py --sku G00b8acf0 --cost 2727 --json
python3 -I ops/price_ceiling.py --table
python3 -I ops/price_ceiling_report.py
```

마지막 명령은 `logs/price-ceiling.html`을 로컬에 만든다. API 서버는 열지 않는다. 공개 분석기의 `catalog.json`·`ledger.json`이 갱신된 후 `python3 -I ops/price_ceiling_cache.py --data-dir ~/work/ax-round4-analytics/data`로 재집계한다. 캐시는 공개 SKU별 집계만 보관한다. 정적 HTML은 재생성해야 바뀐다. 원천 GET 수집과 별개이며 이를 위한 네트워크 권한을 가게 도구에 부여하지 않는다. 자료 시각은 캐시 `sample.end` 및 조회 결과에 나온다.
