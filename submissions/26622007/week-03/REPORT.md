# Week 03 보고서 — 실행 기록과 해석 템플릿

**실제 baseline 1회 완료. 조건 비교와 사용자 해석이 남아 있어 제출용 완성 보고서가 아니다.**
Codex가 실행 하네스와 합성 작업을 작성했다. 아래 결과는 실제 로그에서 옮겼으며 Smith 비교·해석은 사용자가 검토한 뒤 작성한다.

## 1. 설정

- provider: OpenRouter 자동 라우팅. `only`·`order`·`sort` 없음, allow_fallbacks=true, require_parameters=true. 계정 정책과 입력 $0.30/M·출력 $1.20/M 상한을 적용한다.
- 실제 backend는 호출마다 로그의 provider 필드로 확인한다. 라우팅 정책은 세 조건에서 같고 다른 모델로 fallback하지 않는다. 공급자 구현·양자화 차이는 통제하지 못하는 변동 요인이다.
- model: deepseek/deepseek-v4.1-flash.
- temperature=0, max_tokens=512, reasoning.enabled=false 요청.
- A 제품·기술 / B 사업·분석 / C 운영·커뮤니케이션. 정확한 프롬프트는 contract_net.py와 각 실행의 start 로그에 보관.
- 작업당 세 독립 호출, 대화 기억 없음, A→B→C 순서. 최고 확신도에 배정하고 동점은 먼저 입찰한 쪽.
- 명령: `python3 submissions/26622007/week-03/run.py run` (저장소 루트).
- 오류·비용·재시도 정책과 환경은 README.md, config.json, 실행 start 로그를 함께 참고한다.

## 2. 실제 결과

[results.csv](results.csv)의 모든 행을 아래에 표시한다. `—`는 crashed 행의 빈 수치이며 0점이 아니다.
앞선 실패 3회는 다른 공급자 설정이므로 정상 baseline 반복 실험으로 세지 않는다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| 20260915T121020-423c5498 | baseline | — | — | — | — | — | crashed; CallError: HTTP 429 |
| 20260915T121106-1358bb2f | baseline | — | — | — | — | — | crashed; CallError: HTTP 429 |
| 20260915T121151-75d78906 | baseline | — | — | — | — | — | crashed; CallError: HTTP 429 |
| 20260915T121417-95790cf5 | baseline | 6 | 6 | 36 | 0 | 0 | completed; parse_fails=0 |

정상 완료 설정 ID는 `2e2176856b2de858`이며 모든 실제 응답의 모델은 `deepseek/deepseek-v4.1-flash`, 공급자는 Novita였다.
HTTP 18회, 입력 7593 토큰, 출력 1315 토큰, 추론 0 토큰, API 응답 비용 합계 $0.003780636이다. 비용 필드 누락은 0개다.

[정상 실행 로그](logs/20260915T121417-95790cf5-baseline.log)의 낙찰은 17·33·49·65·81·97행, 정답 대조는 98–103행, 집계는 104–105행이다.
순서대로 A, C, B, B, A, C에게 배정됐다. 조건별 추세를 판단할 반복 실험은 아직 없다.
이 완료 결과는 이전 Novita 고정 설정이다. 사용자 요청으로 현재 설정은 자동 라우팅으로 변경했으며 새 설정의 반복 실험과 분리한다.

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
