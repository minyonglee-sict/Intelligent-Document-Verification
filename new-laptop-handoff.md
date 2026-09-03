# 새 노트북 환경 세팅 — Claude에게 전달할 컨텍스트

## 지금 상황
- 프로젝트: Intelligent Document Verification (송장·영수증 검증 파이프라인)
- Git: https://github.com/minyonglee-sict/Intelligent-Document-Verification
- **작업 브랜치: `fixes-redesign-ci`** (master 아님 — 반드시 이 브랜치로 체크아웃)
- **외부 접속 주소가 고정됨: https://idv-check.shop** (가비아에서 산 도메인 + Cloudflare
  Named Tunnel로 연결. 예전 Quick Tunnel처럼 켤 때마다 주소가 바뀌지 않는다.)
- 이 브랜치까지 전부 커밋 + push 완료됨 (최신 코드 커밋: `564963c 품목 표가
  앞표와 합쳐지거나 품목명 열이 중복될 때도 파싱한다`, 그 위에 이 문서를 추가한
  커밋이 하나 더 있음) — `git clone` 한 번이면 소스는 다 받아진다.

## 1. 코드 받기
```powershell
git clone https://github.com/minyonglee-sict/Intelligent-Document-Verification.git
cd Intelligent-Document-Verification
git checkout fixes-redesign-ci
```

## 2. git에 없는 파일 — 직접 옮겨야 함 (USB/개인 클라우드로, GitHub 경유 금지 — 전부 비밀번호·인증서류)

**프로젝트 루트에 그대로 두는 것:**
- `.env` (docker-compose용 MSSQL sa 비밀번호 등)
- `.smtp_credentials` (Gmail 앱 비밀번호, dev.ps1이 읽음 — 1줄: 계정, 2줄: 앱 비밀번호)
- `.figma_token` (쓰고 있었다면)

**⚠️ 새로 추가됨 — `https://idv-check.shop`이 되게 하려면 반드시 필요:**
예전 노트북의 `C:\Users\user\.cloudflared\` 폴더 전체를 새 노트북의 같은 경로
(`C:\Users\user\.cloudflared\`)로 그대로 복사한다. 이 폴더 안 파일들이 없으면
cloudflared가 `idv-check.shop`으로 이어진 그 터널(이름: `idv`)을 못 찾는다:
- `cert.pem` — Cloudflare 계정 인증서
- `145ae0fe-f8da-4a8b-97cb-84ae098016e7.json` — `idv` 터널의 자격증명
- `config.yml` — 터널이 `idv-check.shop` → `localhost:8081`로 가게 하는 설정

**이 폴더를 못 옮기는 상황이면** 새 노트북에서 아래로 새로 인증하고 기존 터널을
그대로 재사용할 수 있다(터널 자체는 Cloudflare 쪽에 이미 만들어져 있음 — 삭제해서
새로 만들 필요 없음):
```powershell
cloudflared tunnel login   # 브라우저 열려서 Cloudflare 로그인 → idv-check.shop 선택
```
로그인만 하면 `cert.pem`은 새로 생기지만, `config.yml`과 터널 자격증명(`.json`)은
그래도 옮겨야 한다 — `cloudflared tunnel token idv`로 다시 받을 수도 있으니 막히면
그쪽으로.

## 3. 설치할 것들
- **Docker Desktop** (WSL2 백엔드) — docker-compose 스택용
- **Node.js** — `frontend/`
- **JDK** — `backend/mvnw`로 Maven 자체는 따로 설치 안 해도 됨
- **Python 3.x** — 엔진용, `pip install -r requirements.txt`
- **Ollama** (https://ollama.com) — 설치 후 모델 다시 받아야 함: `ollama pull qwen2.5:7b` (config.py 기본값. 다른 모델 쓰고 있었으면 그걸로)
- **cloudflared** — `winget install --id Cloudflare.cloudflared -e` (`demo.ps1`가 이걸로 `idv-check.shop` 터널을 엶)
  - ⚠️ 설치 직후 이미 켜져 있던 VS Code 등에서 바로 인식 안 될 수 있음 — PATH가 새로 뜨는 프로세스에만 반영되는 Windows 특성. 앱을 완전히 껐다 켜면 해결됨 (실제로 겪은 이슈, 시간 낭비하지 말 것)
- Git, VS Code
  - Java 확장이 backend/ 처음 열 때 "Null annotation types" 팝업 띄우는데, 이 프로젝트는 null 애너테이션 안 씀 — Disable 눌러도 무방

## 4. 로컬 스택(dev.ps1) vs Docker 스택(demo.ps1)

| | dev.ps1 | demo.ps1 |
|---|---|---|
| DB | 네이티브 MSSQL Server 설치 필요 (Windows 인증) | 컨테이너 안에서 뜸, `.env`만 있으면 됨 |
| RabbitMQ | Windows 서비스로 상시 설치 필요 | 컨테이너 안에서 뜸 |
| 외부 공유 | 안 됨 | `https://idv-check.shop`으로 자동 연결 (고정 주소) |
| 세팅 난이도 | 로컬에 여러 개 따로 설치 | Docker Desktop + `.cloudflared` 폴더만 있으면 됨 |

**추천**: `demo.ps1`(Docker) 쪽이 세팅이 훨씬 간단하다. 네이티브 SQL Server/RabbitMQ를
새로 설치 안 해도 된다. dev.ps1도 계속 쓰려면 MSSQL Server + RabbitMQ를 Windows
서비스로 새로 깔아야 한다.

**실행:**
```powershell
# 1) Docker Desktop 실행 (완전히 뜰 때까지 대기)
cd Intelligent-Document-Verification
.\demo.ps1
# 콘솔에 "외부 접속  https://idv-check.shop" 초록색으로 뜨면 성공
```
종료: `.\demo.ps1 -Stop`

## 5. 겪었던 이슈 (참고만 하면 시간 절약됨)
- 화면은 꼭 `http://localhost:...`로 열 것 — `127.0.0.1`은 Vite 개발 서버가 안 받음 (dev.ps1 로컬 모드 한정)
- `app/`, `api.py`, `mcp_server.py`, `chat_bridge.py` 고치면 재시작 필수 (`--reload` 없음)
- **`docker compose up --build -d backend`처럼 백엔드만 재빌드하면, frontend(nginx)가 예전 백엔드 컨테이너 IP를 그대로 물고 있어서 502가 난다** — 백엔드를 재빌드했으면 `docker compose restart frontend`도 같이 해줘야 한다
- 첫 접속 시 Chrome이 이상한 경고를 띄우면(고정 도메인이라 이제 거의 안 뜨지만, 혹시 뜨면) 도메인이 `idv-check.shop`이 맞는지부터 확인 — 다른 임시 주소(`*.trycloudflare.com`)로 잘못 열었을 가능성
- 메모리 여유 보려면 `.\demo.ps1 -Stop` + Docker Desktop 종료로 확인 (vmmemWSL까지 같이 내려감)

## 6. 남은 할 일 (선택)
- 가비아 도메인(`idv-check.shop`) 자동갱신 여부 확인 — 데모용으로 1년만 쓸 계획이면 자동갱신은 꺼둔 상태가 맞음
- 새 노트북 CPU가 "12세대 이상 i7, H 계열" 기준 맞는지 확인 (설정 → 시스템 → 정보 → 프로세서)
