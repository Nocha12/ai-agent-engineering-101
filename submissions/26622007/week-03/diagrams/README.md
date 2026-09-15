# 현재 구현 아키텍처 · Excalidraw

[Excalidraw에서 공유본 열기](https://excalidraw.com/#json=IWE-iBAx8SMugidp2dBSb,UsFiO02XiwYWsBrvuEU9qA)

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
