# 가게 에이전트의 손익분기 조회

`price_ceiling`은 **계산만** 한다. `max_buy_cost`와 `min_sell_price`는 기본 가정의 개당 정수 크레딧, `expected_profit`은 입력 수량 전체의 예상 현금손익이다. `conservative_*`는 보수 시나리오다. `null`은 가격이 없다는 뜻이다. `buy`/`sell_ok`도 실현손익을 보장하지 않는다. 가게의 매입 중지나 예산·무보증 비식품 금지 정책을 해제하지 않는다.

## 키트 담당자 인계 (지금 등록하지 않음)

1. 다음 승인된 작업에서 손익 모듈·원가 캐시와 `price_demand.py` 및 best_sellers의 공유 모듈·캐시를 한 버전으로 배치한다. 카탈로그·장부 집계는 로컬 공개 캐시에서 읽으며 가게 도구의 시장 요청은 **0회**다. 키트의 `root`가 가게 루트인 것을 먼저 확인한다.
2. `python3 -I ops/price_ceiling_forge.py`로 작은 `forge_tool` 요청을 확인하고 가게 에이전트가 해당 정의를 forge하게 한다. 템플릿은 `ops/forge_templates/price_ceiling/`의 `logic.py`, `test.py`, `permission.yml`이며 지갑·신원·네트워크·파일쓰기 권한이 모두 없다. `tools/`로 직접 복사하거나 `forge.json`을 만들지 않는다. 키트 `scripts/round4/agent_kit.py:284`는 키트 자신의 forge 기록과 일치하는 도구만 로드한다.
3. 매입·가격제시·판매 서명 **직전** 신뢰하는 원본 견적에서 인수를 다시 만든다. 일반 Python에서는 `from ops.price_ceiling import price_guard`, `-I`로 도는 forged tool에서는 `from runpy import run_path; price_guard = run_path('ops/price_ceiling.py')['price_guard']`로 가져온다. 매입은 `price_guard('buy', args, read_buy_state=reader)`, 가격 제시·판매는 `price_guard('price', args)` 또는 `price_guard('sell', args)`를 호출한다. 오류·누락·`skip`·`too_low`이면 거래 전에 종료한다. 과거 조회 결과를 인수로 받거나 실패 시 통과시키지 않는다. 고객에게 이미 지는 환불 의무에는 이 매매 가드를 적용하지 않는다.

**goal 초안:** “매입·판매·가격 제시 전 `price_ceiling`으로 조회하고 실행 직전 원본 견적으로 `price_guard`를 다시 통과하라. `skip`·`too_low`·자료 누락·오류이면 진행하지 말고, 기존 매입 중지와 지갑 한도는 그대로 지켜라.”

**실행 담당자:** 매입 호출은 `price_guard('buy', args, read_buy_state=reader)`이다. `reader(sku)`는 실행 도구의 신뢰된 읽기 함수이지 에이전트가 제출하는 JSON이나 과거 판정이 아니다. 현재 완전히 수집한 공개 원장 끝과 같은 시점의 재고로 `{sku, round: 4, complete: true, observed_at: Unix초, last_seq, own_units, competitor_units, competitor_shops}`를 반환한다. 재고 읽기 전후 원장 끝이 다르거나 페이지 절단·미확정 재고·오류면 `complete: false`로 반환한다. 캐시 시각을 현재 시각으로 덮어쓰지 않는다. 입력이 없거나 15초가 지났거나 캐시와 원장 끝·경쟁 재고가 다르면 매입을 거절한다. 우리 재고는 공개 캐시와 최신 확인값 중 큰 쪽을 차감한다. 리더 연결·캐시 갱신 전에는 조회만 사용한다.

## 입력

- `sku`, `sell`(실제/예상 개당 판매가, 정수) 또는 `cost`(개당 매입 원가). `action` 생략 시 `cost`만 주면 판매, 나머지는 매입 조회다. 두 값을 주고 판매 판정을 보려면 `action=sell`을 명시한다. 하나만 주는 탐색 조회는 반대 가격을 캐시에서 채운다. **가드는 두 가격과 `grade`, `hold_hours`, `round_hours`, `lot_dues`(0도 명시), 식품의 경우 `remaining_hours`를 요구한다.**
- `qty`는 같은 조건의 총 수량, `batch_size`는 1회 매입 수량(최대 20), `lot_dues`는 이 수량에 배분할 기존 보관료·포럼비 합계(견적의 `storage + forum_fees`). `cost`에는 중복 산입하지 않는다. 혼합 lot은 등급·유통기한·가격이 다르므로 한 양호한 단위로 대표시키지 않는다. 가드 호출 전 같은 조건인지 확인하거나 개별 단위에 비용을 보수적으로 배분한다.
- `grade`는 비식품의 매입 등급, `hold_hours`는 매입부터 예상 판매까지 총시간. 식품 `remaining_hours`는 해당 계산의 시작점에서 확인한 잔여시간이며 `hold_hours`도 그 시점부터 센다. 가드에는 식품 잔여시간을 실제 재고/견적의 `expires_at`에서 구해 넣는다. `unsold`는 마감까지 미판매 확률(0~1). 매수 의향만으로 0을 주지 않는다. 등급이 낮아져 실제 흥정가가 바뀌면 `sell`도 갱신한다.
- 단독 조회는 1개·1회·반나절(12시간)·B를 가정한다. 반나절은 예측치가 아니라 재고 운용 시나리오다. `best_sellers`가 넘긴 `hold_hours`가 있으면 그 시간을 그대로 쓴다. 가드는 가정값이 아닌 실제 수량(`qty`), 묶음 크기(`batch_size`), 보유 가능 시간과 마감까지 시간을 요구한다. SKU별 판매가 중앙값(정수 내림), 공급 lot의 `total/qty`를 그 lot 수량만큼 가중한 개당 원가 중앙값을 쓴다. 장부에 개별 등급별 매입 단가가 없어 lot 평균을 사용한다. 데이터가 없으면 정가의 60%·54% 추정이라고 표시한다. `--top`은 두 가격이 모두 실측된 SKU만 비교한다.
- `round_hours`는 실제 마감까지 남은 시간이다. 강의 공지는 15주차만 정하고 정확한 시각은 명시하지 않아 자동으로 날짜를 만들지 않는다. 탐색 조회에서 생략하면 보유시간 동안 라운드가 열려 있다는 가정이다. 가드는 마감 또는 그보다 이른 확인된 거래 중단 시점으로부터 계산한 값 없이는 실행하지 않는다.

## SKU별 판매 확률과 수량

- 최근 시장 전체의 유효 결제 속도 `v`와 6시간 판매 수 `n`은 best_sellers와 공유한다. 0건 또는 속도 0이면 판매 확률 0이다. 그 밖에는 감마-포아송식 수축 `v'=(n+0.25)/(n/v+1)`을 적용한다. `n/v`는 가중 속도의 유효 노출 시간, 시간당 0.25개·노출 1시간은 작은 표본을 완화하기 위한 사전 가정이다. 교정된 예측 확률이나 신뢰구간이 아니다.
- 판매 기회 시간은 `min(보유 가능 시간, 식품 잔여시간, 마감까지 시간)`이다. 기본 점유율은 `1/(경쟁 가게+1)`, 보수 점유율은 그 절반이다. 기본 전망에서는 경쟁 재고가 소진되면 잔여 주문 기회가 우리에게 온다고 보며, 보수 전망에서는 경쟁자가 재입고한다고 보고 품절 이점을 주지 않는다. 기본 `기대판매수=min(q, max(0, max(시장수요×점유율, 시장수요−경쟁재고)−선행재고))`, `판매확률=기대판매수/q`이다. 새 매입은 우리 기존 재고가 먼저 팔린다고 가정한다. 보유 물품 판매 때는 다시 차감하지 않는다.
- 보수 전망에서는 `x=max(0, 시장수요×점유율/2−선행재고)/수량`에 대해 `1−exp(−x)`를 쓴다. 기대 주문 1건을 판매 확률 100%로 오해하지 않도록, 균등하게 나눈 각 물품에 포아송 주문이 적어도 한 번 오는 경우만 인정한다. 독립 주문·균등 배분은 위험 시나리오이지 실측 분포나 통계적 보장은 아니다. 가격이 맞더라도 `best_sellers.buy_qty=0`(최근 6시간 판매 2건 미만 포함)이면 매입하지 않는다.
- `recommended_qty`는 요청 수량과 4개 중 작은 수까지 각각 가스·부대비용을 다시 계산해 보수 기대손익이 가장 큰 수량(동점이면 작은 쪽), 전부 손해면 0이다. **판정과 손익은 추천 수량이 아닌 요청 수량 기준**이다. 수량을 줄일 때 원본 견적을 다시 받아 가드를 통과한다. 명시적 `unsold`는 조회 시 가상 시나리오이며 실행 가드에서는 모델보다 낙관적으로 덮어쓸 수 없다.

## 공식: 가격과 지갑 현금을 혼동하지 않기

`P` 판매가, `C` 원가, `q` 수량, `b` 묶음, `h` 보유시간, `s` 판매 확률(기한 경과 시 0), `d` 판매 시 결함률, `D` 기존 lot 부대비용 합계:

```
F(P) = floor(P/100) + 210
U(P) = P − F(P)                  (고장 환불액 = 가게 실수령액: settle 금액 − 판매 수수료)
R(P) = 0 if P < F(P) else s × [P − F(P) − d × (U(P) + 206)] = s × [(1 − d) × (P − F(P)) − 206d]
K = (206 × ceil(q/b) + D) / q
a = 1 + 0.005 × h (비식품), 1 (식품)
E[전체 현금손익] = q × (R(P) − aC − K)
매입 상한 = floor((R(P) − K) / a), 음수면 없음
판매 하한 = R(P) ≥ aC + K 를 만족하는 최소 정수 P; s=0이면 없음
```

하한은 입력 가능한 범위(0~10¹²)에서 처음 손해가 아닌 가격이다. 시작점은 `F(P) ≥ P/100 + 210 − 0.99`로 구한 닫힌 형식 하한 `floor((B − 1)/k)`(`k=(1−d)×0.99`, `B=(aC+K)/s + 210(1−d) + 206d`)이고, 거기서 1씩 올려 처음 `R(P) ≥ aC+K`인 정수를 찾는다. 환불이 실수령액 기준이면 `P − F(P)`가 `P`에 대해 줄지 않으므로 하한 이상에서 다시 손실이 나지 않는다. 그래도 부등식만 비교하지 말고 실제 제시가로 가드를 호출한다.

환불 기준은 `ops/price_ceiling.py`의 `REFUND_BASIS` 한 곳에서 정한다. 기본 `'net'`(13주차 리허설 선례: 가게가 받은 `P − F(P)`만 돌려줌), `'gross'`는 판매가 전액 환불(`d × (P + 206)`)의 이전 모델이며 그때는 수수료 경계에서 하한 바로 위 가격이 소액 손실일 수 있다.

**확인** — [공식 Patch 4.0](https://market.ourolabs.ai/rules), [공개 시장 서버 62ad46bc](https://github.com/Q00/agent-market/blob/62ad46bc/market/server.py), 키트 `scripts/round4/maroo.py:83`:

- 소매 정산은 양측 각각 1% 내림 + 210. 가게 수입에서는 가게 몫만 빠지며 소매 가스는 구매자 부담(`server.py:934`, `:969`). 도매 매입은 `goods + storage + forum_fees`와 실제 가스다(`:1126`, `:1168`); 판매 수수료를 도매 lot에 덧붙이지 않는다. 같은 lot에서 나뉘는 고정비는 매입 가스와 lot 부대비용이며, 판매 1건당 수수료는 나누지 않는다.
- 비식품은 48시간마다 A→B→C, 매입~판매 합산 결함률은 3/12/35%(`:136`, `:265`, `:1002`). 식품에는 고장 확률이 없고 유통기한 이후 판매 불가(`:295`, `:982`, `:1114`). 마감 평가는 재고 평가액이 아닌 현금이다(과제 `weeks/week-12/README.md`, `server.py:2249`).
- 다음 매입 시점의 남은 비식품 재고 원가와 경과시간을 기준으로 정수 보관료가 붙는다(`:1186`, `:1193`). 미판매 재고의 회수액은 0이다.

**추정** — 비식품은 원가×0.5%×보유시간을 충당한다. 다음 매입 시각에 따라 실제 청구액은 다르다. 우리 원장에 매입 가스 자체 표본이 없어 공개 거래의 약 205~206 중 206을 채택했다. 모든 결함에 가게 실수령액(settle 금액 − 판매 수수료) 환불 및 가스 206을 충당하는 가정이며 강제 환불은 아니다. 잔여시간 미입력 조회는 식품 등급 구간(`:135`) 중점, 보수값은 구간 하단을 적용한다. 명시적 미판매율 시나리오만 10%p 스트레스를 사용한다. 희망 판매가가 과거 실거래가보다 높으면 측정한 속도가 유지된다는 보장은 없다.

## 확인과 갱신

```
python3 -I ops/price_ceiling.py --sku G00b8acf0 --sell 3102 --qty 3 --batch-size 3 --hold-hours 2 --json
python3 -I ops/price_ceiling.py --sku G00b8acf0 --cost 2727 --json
python3 -I ops/price_ceiling.py --table
python3 -I ops/price_ceiling.py --top 5 --hold-hours 12 --round-hours 12 --snapshot
python3 -I ops/price_ceiling_report.py
```

마지막 명령은 `logs/price-ceiling.html`을 로컬에 만든다. API 서버는 열지 않는다. 공개 분석기의 `catalog.json`·`ledger.json`이 갱신된 후 `python3 -I ops/price_ceiling_cache.py --data-dir ~/work/ax-round4-analytics/data`로 재집계한다. 캐시는 공개 SKU별 집계만 보관한다. 정적 HTML은 재생성해야 바뀐다. 원천 GET 수집과 별개이며 이를 위한 네트워크 권한을 가게 도구에 부여하지 않는다. 자료 시각은 캐시 `sample.end` 및 조회 결과에 나온다.

수요 캐시는 동일 수집 회차의 `ledger.json`, `catalog.json`, `txs.json`, `snapshots/`가 필요하다. `D=~/work/ax-round4-analytics/data`로 놓고 `python3 -I ops/best_sellers.py --build --data-dir "$D" --output "$D/best_sellers_data.json"`, 이어 `python3 -I ops/price_demand_cache.py --data-dir "$D"`로 두 도구의 공유 파일 `ops/best_sellers_data.json`을 만든다. 원장·재고 중 오래된 관측이 90분을 넘으면 거래를 거절한다. 키트 설치만으로 캐시가 자동 갱신되지는 않는다. `--snapshot`은 과거 관측 시각에 재현하는 CLI 전용 옵션이며, 실행 가드에는 전달할 수 없다.

공급 단위 ID로 소유자·SKU를 묶어 판매·만료·압류를 차감하고 `/shops`의 판매 가능 재고와 대조한다. `/shops`에 원장보다 많은 재고가 있으면 0개로 간주하지 않고 `stock_unconfirmed`에 넣어 그 품목만 차단한다. `price_guard('buy', …)`는 최근 판매 증거 2건, 점유율·기존 재고를 고려한 `best_sellers`의 수량 한도, 보수 가격 기준을 모두 통과해야 한다.
