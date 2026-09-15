# Week 03 보고서 — 작성 전 템플릿

**실제 OpenRouter 실험 전이며 제출용 완성 보고서가 아니다.**
Codex가 실행 하네스와 합성 작업을 작성했다. 아래 결과·비교·해석은 사용자가 실제 로그를 검토한 뒤 작성한다.

## 1. 설정

- provider: OpenRouter, backend는 deepseek로 고정, fallback=false.
- model: deepseek/deepseek-v4.1-flash.
- temperature=0, max_tokens=512, reasoning.enabled=false 요청.
- A 제품·기술 / B 사업·분석 / C 운영·커뮤니케이션. 정확한 프롬프트는 contract_net.py와 각 실행의 start 로그에 보관.
- 작업당 세 독립 호출, 대화 기억 없음, A→B→C 순서. 최고 확신도에 배정하고 동점은 먼저 입찰한 쪽.
- 명령: `python3 submissions/26622007/week-03/run.py run` (저장소 루트).
- 오류·비용·재시도 정책과 환경은 README.md, config.json, 실행 start 로그를 함께 참고한다.

## 2. 실제 결과

아직 실행하지 않았다. 모의 테스트를 실험 결과로 사용하지 않는다.
실행 후 `run.py summary`의 표를 넣고 crashed 행도 보존한다.

| run | condition | correct/tasks | messages | unassigned | misawards | parse_fails |
|---|---|---|---|---|---|---|

## 3. Smith (1980) 비교 — 사용자가 작성

필수 논문을 읽고 원문 근거와 실제 구현을 대조한다.

| 비교 항목 | Smith의 분산 센싱 시스템 | 이번 구현에서 확인한 내용 |
|---|---|---|
| 참여자 | 작성 필요 | 작성 필요 |
| 입찰 생성 | 작성 필요 | 작성 필요 |
| 입찰의 진실성 보장 | 작성 필요 | 작성 필요 |
| 잘된 배정의 기준 | 작성 필요 | 작성 필요 |
| 협상 비용 | 작성 필요 | 작성 필요 |
| 실패 방식 | 작성 필요 | 작성 필요 |

## 4. 해석 — 사용자가 작성

baseline 대비 어떤 지표가 변했는가? 대표 로그의 파일·줄 번호를 인용한다.
C의 과신 지시가 실제 입찰에 나타났는가? 낙찰이 유지됐다면 동점·확신도·파싱 실패 중 무엇이 설명하는가?
메시지 비용과 HTTP 요청·토큰 비용은 어떻게 다른가?
여기서 gold는 실제 업무 수행 성능이 아니라 사전 역할 정의에 따른 책임 담당자다. 이 평가 범위와 6개 합성 작업의 한계를 명시한다.
