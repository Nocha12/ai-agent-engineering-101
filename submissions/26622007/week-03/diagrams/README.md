# 현재 설계 · Excalidraw

## 멀티 에이전트 설계 관점

[멀티 에이전트 설계 공유본](https://excalidraw.com/#json=FZ2X8m1r9bWg6MMVcxxTD,0_U2FcQXtrJWVCYhb9Elzg) · [편집 가능한 원본](multi-agent-design.excalidraw) · [확인 화면](multi-agent-design-shared-preview.png)

사용자가 요청한 협업 설계 관점의 도식이다. baseline의 전문 역할을 표시하며, 세 조건의 전체 통신 구조는 같다.

- **주체와 권한:** Manager가 최종 배정을 결정하고, A/B/C는 자신의 참여 여부와 확신도를 판단한다.
- **통신 구조:** 중앙 관리자를 중심으로 공고와 입찰이 왕복한다. 에이전트끼리는 입찰을 공유하지 않는다.
- **정보 범위:** 각 에이전트는 자기 역할과 현재 공고만 받는다. 관리자는 현재 작업의 입찰들을 모은다.
- **컨텍스트 수명:** 다음 업무에서는 대화·입찰이 새로 시작된다. 배정 기록은 실행 단위로 보존하지만 다음 입찰에 전달하지 않는다.
- **협상 범위:** 한 라운드의 입찰로 담당자를 결정한다. 후속 업무 수행 호출은 현재 없다.
- **평가 경계:** 외부 평가자가 실행 종료 후 배정과 사전 정답을 대조한다. 정답과 평가 결과는 협상에 들어가지 않는다.

Excalidraw에서 77개 요소를 불러오고 공유 링크를 새 브라우저에서 다시 열었다. 원본과 요소 ID 77개·텍스트 49개가 일치했다. 확인 시각: 2026-09-15T12:45:11.371Z.
공유 링크는 내보낸 시점의 스냅샷이다. 이후 편집은 파일로 저장해서 보존한다.

재생성: `python3 submissions/26622007/week-03/diagrams/build_agent_design.py`

## 프로그램 구성 관점의 이전 도식

[이전 프로그램 구성 도식 열기](https://excalidraw.com/#json=IWE-iBAx8SMugidp2dBSb,UsFiO02XiwYWsBrvuEU9qA)

- [편집 가능한 원본](week-03-architecture.excalidraw): 도형·화살표·텍스트로 구성된 네이티브 파일.
- [공유본 화면 확인](architecture-shared-preview.png): 새 브라우저에서 공유 링크를 열어 확인한 화면.
- 공유 링크는 내보낸 시점의 스냅샷이다. 수정 내용을 보존하려면 Excalidraw 메뉴에서 파일로 저장한다.

## 포함한 내용

관리자 코드, 현재 A 제품·기술 / B 사업·분석 / C 운영·커뮤니케이션 역할, 독립 호출,
OpenRouter 자동 라우팅, JSON 입찰 검증, 담당자 선정, gold를 분리한 평가, 원본 로그와 CSV를 표시했다.
하단에는 메시지 규약·컨텍스트 수명·세 실험 조건·실제 baseline 결과를 정리했다.
선정 후 업무 수행 호출은 현재 구현되지 않았음을 표시했다.

## 검증

- 현재 소스 `3de58e6`의 contract_net.py, run.py, openrouter_client.py, config.json을 확인했다.
- Excalidraw 웹 편집기에서 83개 요소를 가져왔으며 한글·텍스트·화살표를 시각적으로 확인했다.
- Export to Link로 공유본을 만든 뒤 별도의 새 브라우저 컨텍스트에서 다시 열었다.
- 공유본의 요소 ID 83개와 텍스트 50개가 원본과 일치했다. 확인 시각: 2026-09-15T12:36:47.364Z.

## 다시 생성

저장소 루트에서 Python 표준 라이브러리로 원본을 생성한다.

```bash
python3 submissions/26622007/week-03/diagrams/build_architecture.py
```

`excalidraw_browser.cjs`는 이번 가져오기·공유 검증에 사용한 Playwright 보조 스크립트다.
현재 저장된 공유본은 스냅샷이므로 원본 파일을 다시 생성해도 자동 갱신되지 않는다.
