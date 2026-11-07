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
