# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code(claude.ai/code)를 위한 안내다.

## 이게 무엇인가

송장·영수증 검증 파이프라인이다. 문서를 올리면 Docling이 텍스트를 뽑고, 표 파서가
품목을(LLM 없이) 읽고 Ollama가 머리말을(LLM으로) 뽑은 뒤, 결정적 규칙이 전부
검증해서 MS SQL Server에 쌓고, 사람이 검수·승인한다. **세 진입점이 같은 엔진과 같은
DB를 공유**한다 — Streamlit(`main.py`), React+Java(`frontend/` + `backend/`, 엔진의
HTTP API를 부름), 그리고 MCP 서버(`mcp_server.py`, Claude Desktop과 화면의 채팅 탭이
둘 다 씀 — 화면 쪽 채팅 루프는 `chat_bridge.py`가 로컬 Ollama 모델로 직접 돌린다).

## 개발 스택 띄우기

```powershell
.\dev.ps1              # 엔진(8000) + Java 백엔드(8080) + React 화면(5173) 셋 다 기동
.\dev.ps1 -Stop        # 셋 다 종료 (창까지 같이 닫는다)
```

화면은 `http://localhost:5173`로 열 것 — `127.0.0.1`은 안 된다. Vite가 `[::1]`에만
붙는다.

**`app/`, `api.py`, `mcp_server.py`, `chat_bridge.py` 중 하나라도 고치면 재시작
필수.** 엔진은 `--reload` 없는 순수 `uvicorn`이라, 재시작 전까지 옛 코드를 그대로
메모리에 물고 있다 — 엔진이 시작할 때 자식 프로세스로 띄우는 MCP 서버도 마찬가지다.
화면 코드(`frontend/src/**`)는 Vite HMR로 바로 반영되니 재시작 불필요.

`.\dev.ps1` 실행이 스크립트 실행 정책 에러로 막히면, 이 프로젝트 문제가 아니라
PowerShell `CurrentUser` 기본값(`Restricted`)이다:
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

한 계층만 따로 띄우려면 `dev.ps1`이 실제로 돌리는 명령을 보면 된다
(`python -m uvicorn api:app --port 8000`, `backend\mvnw.cmd -B spring-boot:run`,
`frontend/`에서 `npm run dev`) — 그래도 한 계층만 고쳤어도 `dev.ps1`을 쓰는 게 낫다.
다른 계층을 재시작 안 해서 실제로 문제가 난 적이 있다(Java를 안 띄워 엔진에 새로
생긴 경로가 404로 나온 사례).

## 빌드 / 린트 / 타입체크

```powershell
# 화면
cd frontend; npm run lint; npx tsc -b --noEmit; npm run build

# 백엔드 — 있는 테스트는 Spring "컨텍스트 로딩" 스모크 테스트 하나뿐
cd backend; .\mvnw.cmd -B test

# 엔진 — 테스트 스위트 자체가 없다. 문법만이라도:
python -m compileall -q app api.py mcp_server.py chat_bridge.py main.py
```

Python 쪽엔 테스트 스위트가 없고, "테스트 하나만 돌리는" 방법도 없다. CI
(`.github/workflows/ci.yml`)가 `master`로 push·PR 때마다 이 세 가지를 그대로
돌린다. CD는 아직 없다 — 배포 대상(서버/컨테이너)이 정해지지 않았다.

화면 없이 배치 처리: `python run_pipeline.py 파일.pdf` 또는
`python run_pipeline.py data\uploads\*.pdf --force` (파일 해시가 이미 있어도 강제
재처리).

## 아키텍처

### 추출과 검증을 "무엇을 믿을 수 있는가"로 갈랐다

LLM 출력을 그대로 진실로 안 믿는다. 품목은 `table_parser.py`가 Docling의 Markdown
표를 직접 읽어서 뽑는다(정규식·열 매칭, LLM 호출 0회) — 예전에 LLM으로 품목을
뽑아봤지만 느리고 부정확해서 되돌렸다. 머리말(송장번호·날짜·총액)만 Ollama를
거친다 — 문서마다 자리가 달라 규칙으로 못 잡기 때문이다. 추출 이후의 모든 것 —
`rule_check`(산술·날짜·필수값)과 `grounding_check`(뽑은 값이 원문에 실제로
있는지) — 는 **LLM이 전혀 관여하지 않는** 결정적 규칙 코드라, 같은 입력이면 항상
같은 판정이 나온다. `probe_missing_fields`(빈 필드 LLM 재확인)는 검증이 아니라
추출 단계에 있다 — 거기서 찾은 값을 그 자리에서 채워야 저장까지 이어지기
때문이다.

`layout_recovery.py`는 Docling이 페이지 넘어가며 쪼갠 품목을, Markdown 순서로
추측하지 않고 원시 Docling JSON의 조각별 좌표(`prov.bbox`)로 되살린다. 서로 다른
두 가지 깨짐 모양에 대응하는 독립된 복원 경로 두 개가 있다: 번호 달린 품목이
낱말 단위로 흩어진 경우(`"7."` 같은 번호 조각을 닻으로 삼아 근처 조각을
y좌표로 모음) vs. 품목 번호 열 자체가 없어 조각 하나가 이미 행 전체인 경우
(자리 순서로만 열을 가름). 두 경로 다 `수량 × 단가 = 금액` 검산을 통과해야만
받아들인다 — 검산 안 되는 값은 절대 지어내지 않는다. 덜 살린 문서는 검증이
잡아서 검수자에게 보여주는 게, 틀린 숫자를 조용히 받아들이는 것보다 낫다.

`table_parser.map_columns()`는 헤더 문자열을 `COLUMN_HINTS`로 매칭할 때 **완전
일치를 부분 일치보다 먼저** 본다 — 안 그러면 "Product ID" 열과 "Description" 열이
둘 다 있는 문서에서 "product"가 ID 열에 먼저 부분 일치해 품목명 자리를 뺏는다.

### 상태는 자유 텍스트가 아니라 한 방향 상태 머신이다

`PROCESSING → (ERROR | PENDING) → VALIDATED`, 추출/LLM 자체가 실패하면 종착점인
`FAILED`. `VALIDATED`는 승인 버튼을 거쳐야만 도달한다 — 자동으로 완료되는 경로는
없다. 재검증(검수자가 값을 고친 뒤 다시 검증)은 필드와 오류 행만 갱신하고 상태
자체는 절대 안 바꾼다 — 바꾸면 고치던 문서가 승인되기도 전에 검수 목록에서
빠져버린다.

### 세 진입점은 하나의 MCP 도구 계층을 공유해서 서로 어긋나지 않는다

`mcp_server.py`는 `app/` 함수들을 MCP 도구로 감싼다(`list_documents`,
`get_document`, `reparse_document`, `resolve_report` 등). **승인·삭제·업로드는
일부러 안 낸다** — 이건 사람이 화면에서 직접 해야 한다는 설계이며, 이유는 그
모듈 자체의 docstring에 적혀 있다. `chat_bridge.py`는 Claude Desktop이 내부에서
하는 것과 같은 도구 호출 루프를, 로컬 Ollama 모델로 그대로 구현한다: 질문 +
도구 목록을 보냄 → 모델이 도구를 요청하면 같은 MCP 세션으로 실행하고 결과를
돌려줌 → (`MAX_ROUNDS = 5` 한도로) 반복 → 모델이 도구 요청 없이 답할 때까지.
읽기 전용 도구는 화면 갱신이 필요 없지만, 데이터를 바꾸는 유일한 도구
`resolve_report`가 쓰이면 채팅 패널이든 신고 패널이든 앱의 `refresh()`를 반드시
불러야 한다 — 안 그러면 새로고침 전까지 상태 배지가 낡은 채로 남는다.

오류 신고(화면에서 접수하는 버그 신고)는 **DB 행이 아니라 파일**이다 —
`data/reports/{번호4자리}_{시각}_doc번호/`. DB 자체가 고장 났을 때도 신고할 수
있어야 하기 때문에 일부러 이렇게 만들었다. `report.resolve_slug()`로 사람이나
LLM이 전체 폴더명 대신 짧은 번호(`"3"`, `"#3"`)만으로도 신고를 가리킬 수 있다.

### Java 백엔드는 업무 로직이 없는 순수 통로다

`DocumentController.java`엔 자체 검증 규칙이 없다 — 모든 엔드포인트가
`EngineClient`를 거쳐 Python 엔진의 HTTP API(`api.py`)로 그대로 넘기고, 응답도
그대로 돌려준다. 업무 로직을 고칠 일이 있으면 `backend/`가 아니라 `app/`을 고쳐야
한다.

세 진입점 전부 같은 DB를 봐야 한다 — `dev.ps1`이 셋 다 `MSSQL_DATABASE`를
`DocumentVerification_Dev`로 고정해서 띄운다. 화면마다 다른 DB를 보면 같은
문서가 화면마다 다르게 보인다.
