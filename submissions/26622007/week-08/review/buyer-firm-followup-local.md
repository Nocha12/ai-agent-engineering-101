# 후속 고수가격 후보 — 구현 전 원인

기준 80ab21d, clean 확인. 최신 직접 지시 19:32 우선, work-state는 19:31을 반환.
원인: oneshot.text_price의 TEMPLATE/ask_read 범위 밖인 고수가격 후속 11행이
buyer.talk의 ladder=None 결과를 통해 harness로 넘어간다. 첫 quote는 이미 CODE다.
독립 helper의 전체 문형 일치와 기존 history 일치만 추가하고 code_turn 정책은 재사용한다.
기존 owner/quote alignment/seller/restock/meta/hard_limits는 변경하지 않는다.

|가설|선택/기각|근거|
|---|---|---|
|상태 캐시|기각|history/cap 변화, 첫 행 hash는 상태 동일성 증거 아님|
|지급 가격/보증 보정|기각|10640 실패는 수명 461.78 < 540.1h|
|날짜/잔여시간 parser 확대|기각|3행 다중행 ask 변경, 단일 변수 범위 밖|
|후속 고수가격 문형|선택|11행/5취소 기회 상한, 경제 효과 미입증|

승인된 시험 경계: pure helper 공개 함수와 buyer.talk의 fake RPC/LLM 경계.
원래 조건은 실제 시장 협상·지급, 이번 조건은 저장 입력의 순수 재생·합성 fake talk다.
80ab 전체 857 HOLD(환경 직접31/미확정5)는 통과 근거로 사용하지 않는다.

## 차단 인계

준비 커밋: 3cb7fec. 제품 코드 변경 0. 첫 시험은 미구현 helper import를 요구하는
tracer test이며 현재 GREEN이 아니다. 테스트 제외·전체 PASS 주장은 하지 않는다.
검증 명령: verify-receipt run --work-key W-20261106-1950-AX바이어점수
--label firm-red --expect-fail -- python3 -B -m unittest test_buyer_firm_followup
영수증 기록 실패: PermissionError [Errno 1] Operation not permitted.
거절 위치는 중앙 receipts 디렉터리의 atomic tempfile 생성이다.
wrapper 종료코드 1; 영수증 미생성, RED 증거 인증 불가. 같은 동작 재시도/도구변경 0.
최종 receipt check 및 11행 전후/fake talk 검증은 미실시다.
이 실패를 제품 실패나 기대 RED 성공으로 판정하지 않는다.

정적 설계(미구현): 단일 pure turn helper는 기본 arm=None으로 OFF,
meta가 있거나 history가 없거나 정책 통과가 명시되지 않으면 None.
전체 문형의 양수 단일 ask가 직전 ask와 같고 최근 offer 이상인 경우에만
기존 code_turn으로 전달한다. cap<=0/비정상 수치/새 card/보증 부족은 fail closed.
기존 price_ladder 결과가 None일 때만 연결하며 기존 정책 결과는 보존한다.

실험 장치 미구현·활성화 0: 향후 source 고정창이 필요하다.
부모가 독립 검토 후 확정할 활성 필드는 enabled=false, study, start, end=start+1800,
expiry_off=true, 기존 owner/quote 후보와 비중첩인 conflict guard다.
order별 최초 task를 결합해 stable hash 1:1 확률 배정하고 retry/task 변경에 재배정하지 않는다.
배정은 결과 관측 전에 기록하고 취소/실패/무지급/타임아웃을 ITT 전체 주문 분모에 남긴다.
유한 표본의 정확한 1:1 건수 균형은 hash만으로 보장하지 않는다.
19:11~41/19:51:05 seller 보호창 변경 0. 미래 30분 창은 아직 지정/활성화하지 않았다.
OFF/expiry 시 신규 배정 중지, 기존 활성 협상 drain 확인 후 전환해야 한다.
rollback은 신규 배정 OFF와 drain 후 후보 연결 제거이며 현재는 운영변경 없어 불필요하다.

실AB 0 · 독립리뷰 미실시(부모 단계) · 전체목표 미완료 · 배포 불가.
full gate는 기존 market_buyer callback에 연결하는 제품 변경의 배포 전에 필요하다.
독립 pure 검증만으로 전체 wrapper/시장지급/운영 안전을 대체하지 않는다.
328246 군은 조기 walk 변화가 예상되며 성사보존 입증이 아니다.

쓴 스킬: gstack-investigate, mp-tdd, gstack-review, work-review 원문 확인.
review는 diff 검토 준비까지이며 독립 리뷰 통과를 주장하지 않는다.

리뷰 결과: 위험도 저 · 확인된 제품 지적 0건 · 미해결 검증 차단 1건 · 독립리뷰 미실시
검증: firm-red wrapper rc1, 영수증 저장 권한 거절; GREEN/11행/fake talk/check 미검증
보안: 제품·운영·키·원격 변경 0; 원문 문형 도구출력 0
정정 대조: 19:32 우선; 857 HOLD 유지; 첫 quote 중복/날짜 확대 제외; 지급 수명 원인 유지
되돌리는 법: 준비 커밋을 git revert로 되돌림, 이력 재작성 금지; 운영 변경 없음
