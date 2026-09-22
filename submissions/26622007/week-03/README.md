# Week 03 — 계획 심사와 재귀 위임 하네스

[REPORT.md](REPORT.md)는 현재 peer DAG 코드와 출력 상한 없는 45회·별도 복구 16회를 기준으로 작성한다.
최초 배정, 하위 실제 위임, 최종 산출물을 함께 평가한다. 과거 배정 전용 코드의 보고서는
[참고 기록](REPORT_ALLOCATION_REFERENCE_20260922.md)으로 보존했으며 현재 결과와 합산하지 않는다.

2026-09-22 정정: 현재 기본/peer DAG 설정은 `max_tokens`와 `max_completion_tokens`를 요청에 넣지 않는다.
이전 peer 실험의 2200토큰 상한은 사용자가 요청한 조건이 아니라 구현 에이전트가 임의로 넣은 설정이었다.
아래 과거 28/45 결과에는 그 상한으로 잘린 17회가 포함되어 있으므로 상한 없는 시스템의 성능으로 해석하지 않는다.
동일 45개 슬롯의 새 실험을 [출력 상한 제거 규약](extensions/peer_dag/conditions/SUITE_NO_TOKEN_LIMIT_PROTOCOL.md)에 따라 완료했다.
제공업체 자체 기본 한도와 JSON Schema/전송/작업 자원 검증은 유지한다. 과거 설정과 원본 결과는 보존한다.

**현재 작업 목록: 복합 과제 5개. 출력 상한 없는 본 실험 45회와 429 복구 16회를 완료했다.**

[최신 45회 결과](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md)와
[산출물 품질 점검](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/QUALITY_REVIEW.md)을 확인한다.
본 실험의 필수 facts 통과는 29/45이며 나머지 16회는 HTTP 429 소진 실패다.
[별도 복구](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/REPORT.md)는 15/16이 facts를 통과했다.
복구까지 포함하면 원래 45개 슬롯 모두 최종 문서를 확보했고 44개는 필수 facts가 맞다. 이 44/45는 최초 시도의 성공률이 아니다.
남은 한 건은 공헌이익과 개발 여유 시간의 의미 충돌이다. 전체 829개 반환 응답은 JSON Schema 검사를 통과했고 잘림 없이 종료됐다.
[과거 2200토큰 실험](extensions/peer_dag/conditions/20260921T113158-suite-ab4e67/FINAL_REPORT.md)의 28/45 기록은 별도로 보존했다.
기본 강의 배정 실험은 별도 [규약·실행기](task_sets/complex-final/PROTOCOL.md)와 [이전 보고서](REPORT_ALLOCATION_REFERENCE_20260922.md)에서 다룬다.

현재 peer DAG에서는 DeepSeek V4.1 Flash를 사용하는 A/B/C가 모두 계획·심사·실행·위임·통합을 수행한다.
고정 관리자 전용 에이전트 없이 각 작업의 요청자가 관리자 역할을 맡는다. [현재 구조와 실행](extensions/peer_dag/README.md)을 참고한다.
출시 검토, 게임 기획·코드 구조 설계, 결제 시스템 재설계, 서비스 확장, 출시 운영을 포함한다.
[작업·gold 설계](TASK_DESIGN.md)와 [실제 계획·수행용 사례](extensions/peer_dag/cases/README.md)를 참고한다.
같은 사건이라도 요구하는 결과물에 따라 적임자가 달라지는지 관찰한다.
실제 고객 응대·결제·배포를 실행하지 않는다. 사전 gold와의 최초 담당자 일치, 실제 위임·병렬 실행, 최종 수치와 문서 품질을 구분해 측정한다.
아래의 `contract_net.py`·`run.py`와 기본 명령은 과거 배정 전용 구현을 재현하는 참고 설명이다.

## 이전 작업 목록의 실제 테스트 결과

아래 기록은 [기존 6개 출시 업무](task_sets/launch-v1/tasks.json)의 과거 실행이다. 새 5개 작업의 검증 결과가 아니다.

새로 입력한 사용자 키로 OpenRouter의 **여러 공급자 자동 라우팅**을 사용했다.
관리자는 코드로 동작하고, A·B·C만 독립 LLM 호출이다. 외부 프레임워크와 장기 기억은 없다.

| 조건 | 작업 | 정답 배정 | 메시지 | 미배정 | 오배정 | JSON 실패 | HTTP 요청 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline 1회 | 6 | 6 | 35 | 0 | 0 | 0 | 18 |

- 실행 `20260915T121722-fb1a7bb5`, 설정 ID `0483e14314802b34`.
- 18개 응답이 실제로 9개 공급자에 분산됐다. 모든 응답 모델은 `deepseek/deepseek-v4.1-flash`였다.
- API 응답 비용 합계 **$0.003295322**. 위 18개 응답 기준이며 사전 연결 점검과 과거 실행은 별도다.
- [원본 로그](logs/20260915T121722-fb1a7bb5-baseline.log), [모든 성공·실패 행](results.csv). 공급자 분포와 과거 설정은 해당 원본 로그에 보존돼 있다.
- 앞선 HTTP 429 실패 3회와 Novita 고정 성공 1회도 보존했다. 설정이 달라 현재 조건 비교의 반복 횟수에 포함하지 않는다.
- 이 자동 라우팅 결과는 과거 기록이며 현재 Fireworks 고정 5개 작업 실험과 섞지 않는다. REPORT의 Smith 비교·해석은 사용자 검토가 필요하다.

## 기본 배정 구현의 참고 구조

[멀티 에이전트 설계: 역할·통신·기억·권한](https://excalidraw.com/#json=FZ2X8m1r9bWg6MMVcxxTD,0_U2FcQXtrJWVCYhb9Elzg) · [편집 가능한 원본](diagrams/multi-agent-design.excalidraw)

[프로그램 구성 관점의 이전 도식](https://excalidraw.com/#json=IWE-iBAx8SMugidp2dBSb,UsFiO02XiwYWsBrvuEU9qA) · [편집 가능한 원본](diagrams/week-03-architecture.excalidraw) · [다이어그램 설명](diagrams/README.md)

```mermaid
flowchart TD
    R[run.py: 조건과 실행 초기화] --> M[관리자: 작업 공고]
    M --> A[A 제품·기술]
    M --> B[B 사업·분석]
    M --> C[C 운영·커뮤니케이션]
    A --> V[독립 응답 검증]
    B --> V
    C --> V
    V --> W[최고 확신도에 낙찰]
    W --> E[평가자: gold 대조]
    E --> L[원본 로그와 results.csv]
```

| 파일 | 책임 |
|---|---|
| `contract_net.py` | 역할·공고, JSON 입찰 검증, 낙찰과 집계. API·인증 정보를 모른다. |
| `openrouter_client.py` | OpenRouter 호출, 명시한 공급자·response_format 전달, 시간·재시도·총 호출 제한 |
| `run.py` | 실행 초기화, 로그, CSV, 설정·소스 해시, 실제 실행 요약 |
| `config.json` | 모델·온도·토큰·추론·공급자·오류 정책 |
| `tasks.json`, `TASK_DESIGN.md` | 사전에 커밋하는 작업과 정답 근거 |
| `test_harness.py` | API를 쓰지 않는 모의 응답 검증. 실험 데이터와 분리 |

에이전트의 역할 설정은 실행 동안 유지하고 대화는 **작업×에이전트마다 새로 생성**한다.
관리자의 입찰 목록은 작업마다, 통계는 실행마다 초기화한다. 과거 낙찰, 정답, 평판을 다음 모델 입력에 넣지 않는다.
API 호출용 객체는 재사용하지만 대화 기록은 보관하지 않는다.

## 실행 환경과 키

Python 3.10 이상, macOS/Linux. 기본 실행은 표준 라이브러리를 사용하고 응답 스키마/실험 검증기는 `jsonschema` 패키지가 필요하다.
아래 명령은 저장소 루트에서 실행한다. 이미 있는 `.venv/bin/python`으로 `python3`를 대체해도 된다.

키는 상위 학번 폴더의 로컬 `.env`에 `OPENROUTER_API_KEY` 항목으로 설정한다. `.env.example`은 빈 형식 참고용이다.
기존 다른 키 항목을 유지하고 OpenRouter 항목만 추가하면 된다.
우선순위는 `--env-file` → 상위 학번 폴더 `.env` → 환경변수 `OPENROUTER_API_KEY`다.
파일이 있으면 그 파일의 설정만 사용하고, 값이 비었거나 잘못됐어도 다른 키로 자동 대체하지 않는다.
기존 파일의 `OPENAI_API_KEY`도 OpenRouter 키 형식일 때만 호환된다. 셸의 일반 OpenAI 키는 읽지 않는다.
키는 로그·명령 인자·Git에 넣지 않는다. 기존 다른 서비스의 키를 수정할 필요는 없다.
공식 검사기는 Git에서 무시하는 파일도 검사하므로 **실제 키 파일은 week-03 안에 두지 않는다.**

```bash
# 네트워크 호출 없이 설정·예상 호출 수·키 형식만 확인
python3 submissions/26622007/week-03/run.py plan

# 모의 응답 테스트: 실제 API 요청과 과제 results.csv를 만들지 않는다
python3 -m unittest discover -s submissions/26622007/week-03 -p 'test_*.py' -v

# 실제 연결 점검: 첫 작업에 3회 호출, diagnostics/에만 기록
python3 submissions/26622007/week-03/run.py smoke

# 현재 목록 본 실험: 세 조건 × 3회 × 작업 5개 × 에이전트 3명 = 135회
python3 submissions/26622007/week-03/run.py run

# 과거 512토큰 배정 실험의 설정·회전 순서 재현(현재 기본 설정과 별도, 새 결과 행 추가)
python3 submissions/26622007/week-03/task_sets/complex-final/allocation_study.py run

# 특정 조건만 추가 실행. 이전 실패 로그와 CSV 행은 그대로 유지한다
python3 submissions/26622007/week-03/run.py run --condition baseline --repeats 1

# 실제 기록을 Markdown 표로 보기
python3 submissions/26622007/week-03/run.py summary

# 공식 과제 형식 검사(내용 해석의 완성도를 심사하는 검사는 아님)
python3 scripts/check_week03.py submissions/26622007/week-03
```

## 기본 배정 구현의 고정 조건

- 모델 `deepseek/deepseek-v4.1-flash`, OpenRouter 주소는 코드에 고정.
- 공급자 `only=["fireworks"]`, `allow_fallbacks=true`, `require_parameters=true`. 허용 목록 밖 공급자로 전환하지 않는다. 과거 자동 라우팅 기록과 구분한다.
- 모델 목록 fallback은 사용하지 않는다. 어떤 공급자가 선택돼도 요청 모델 ID는 동일하며 실제 응답의 provider/model/usage를 기록한다.
- 조건 간 고정 대상은 라우팅 정책이다. 공급자별 구현·양자화 차이가 영향을 줄 수 있으므로 결과 해석 때 실제 공급자 분포도 확인한다.
- 현재 기본 설정은 temperature=0, reasoning.enabled=false이며 출력 토큰 상한 필드를 생략한다. 과거 배정 실험의 별도 설정은 재현용으로 max_tokens=512를 보존했다.
- 입찰에는 `response_format.type=json_schema`, `strict=true`의 bid/confidence/reason 스키마를 명시한다. 실제 요청 payload와 응답을 검증하며 로컬 파싱·의미 검사를 유지한다. 응답을 임의로 수리하지 않는다.
- 기본 config: 요청 30초, 응답 수신 기한 90초, 통신·일부 HTTP 오류 최대 2회, 한 명령 최대 324회 HTTP 요청. 최종 실험의 별도 config는 429 최대 6회·누적 대기 300초와 총 HTTP 상한 810회를 사용한다. 실제 9회 설정은 각 start 로그에 기록한다.
- 공급자 토큰 단가 상한: 입력 $0.30/M, 출력 $1.20/M. 실제 비용·추론 토큰은 응답 usage에 있으면 기록한다.
- 설정·프롬프트·작업·소스 코드 해시로 experiment_id를 남긴다. 서로 다른 experiment_id를 하나의 조건 비교로 합치지 않는다.
- `tasks.json`이 HEAD에 커밋된 내용과 같아야 실제 호출을 시작한다.

| 조건 | 유일한 변경 |
|---|---|
| baseline | A 제품·기술, B 사업·분석, C 운영·커뮤니케이션 |
| homogeneous | 세 명의 능력 설명만 같은 general problem solving으로 변경 |
| overconfident | baseline C에 모든 작업에 confidence 95 이상으로 입찰하라는 문장 추가 |

## 평가와 실패 처리

`correct + misawards + unassigned = tasks`이고 정확도는 `correct / tasks`다.
메시지는 공고 3건/작업 + 유효한 `bid=true` 개수 + 낙찰 1건으로 센다.
강의 예제에 맞춰 `bid=false`, 파싱 실패, HTTP 재시도는 협상 messages에 더하지 않는다.
대신 refusals, parse_fails, http_requests를 note에 별도로 남긴다. API 호출 수와 messages는 서로 다르다.

JSON 구문·필드·타입·범위 위반과 빈 응답은 입찰 없음으로 처리한다. 실패를 고치려고 다시 모델에 묻지 않는다.
API 장애로 실행을 끝낼 수 없으면 로그와 빈 수치의 crashed 행을 남기고 전체 명령을 중단한다.
재실행은 새 run_id로 시작한다. 같은 출력 폴더에 동시에 두 실행을 쓰는 것은 잠금으로 막는다.
reported_cost_usd는 API가 제공한 비용 합계다. 누락 응답·통신 실패까지 포함한 청구서 금액은 아니다.

## 검증 상태

출력 상한 제거 후 오프라인 검사 96개(기본 30, peer 49, 중단된 research 호환성 17)와 429 복구 대상 선정 검사 5개를 통과했다.
[61회 증거 재검증](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/evidence_verification.json)은 원본 로그에서 지표를 재계산하고 실제 요청의 상한 부재·strict response_format·복구 대상 일치를 확인했다.
기본 배정의 세 조건 × 3회도 별도 완료했으며 당시 512토큰 설정은 과거 재현용으로 보존했다. 자세한 결과는 [이전 보고서](REPORT_ALLOCATION_REFERENCE_20260922.md)에 있다.
공식 과제 형식 검사는 [검사 로그](logs/20260922-no-token-limit-course-check.log)로 확인한다. 자동 facts 검사와 형식 검사는 문서 내용의 정확성이나 아래 학습 부분의 완성을 보장하지 않는다.

## 사용자가 마무리할 학습 부분

1. `TASK_DESIGN.md`의 gold와 역할 정의를 직접 검토하고 수정 필요 여부를 판단한다.
2. 실제 로그를 읽고 과신·일반화·동점·파싱 실패가 각 지표에 미친 영향을 해석한다.
3. `REPORT.md`의 Smith 비교와 해석을 본인의 근거와 표현으로 작성한다.

참조: [과제 명세](https://github.com/Q00/ai-agent-engineering-101/blob/main/weeks/week-03/README.md),
[강의](https://wpti.dev/ai-agent-engineering-101/week-03.html),
[OpenRouter 모델](https://openrouter.ai/deepseek/deepseek-v4.1-flash),
[공급자 라우팅](https://openrouter.ai/docs/guides/routing/provider-selection),
[추론 설정](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
