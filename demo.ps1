<#
.SYNOPSIS
    docker-compose 스택(Docker Desktop) + Cloudflare Quick Tunnel을 한 번에 띄운다.

.DESCRIPTION
    docker-compose.yml 은 화면(8081)까지 컨테이너로 띄우지만, 그 자체로는 로컬에서만
    열린다. 외부(폰, 다른 PC)에 데모로 보여주려면 cloudflared 로 터널을 따로 열어야
    하는데, docker-compose.yml 에는 그 서비스가 없다 -- Quick Tunnel 은 실행할 때마다
    임시 주소를 새로 받는 방식이라 컨테이너처럼 고정해 둘 수가 없기 때문이다.

    그래서 이 스크립트가 두 단계를 순서대로 묶는다:
      1) docker compose up --build -d   (mssql/rabbitmq/engine/worker/backend/frontend)
      2) 세 계층이 응답하기 시작하면 cloudflared 를 새 창에서 연다

    cloudflared 창은 계속 떠 있어야 터널이 산다 -- 닫으면 그 순간 주소가 죽는다
    (재발급 없이 그냥 사라진다). 실행마다 새 주소가 나오니, 뜬 창에서 URL을 그대로
    복사해 쓴다.

    docker-compose.yml 자체의 주의사항(.env 준비, 포트 겹침)은 그대로 유효하다 --
    이 스크립트는 그 위에 터널 단계만 얹은 것이다.

.EXAMPLE
    .\demo.ps1            # 컨테이너 + 터널 기동
    .\demo.ps1 -Stop       # 컨테이너 + 터널 종료
    .\demo.ps1 -NoTunnel   # 컨테이너만 띄우고 터널은 안 연다 (로컬 확인용)
#>
param(
    [switch]$Stop,
    [switch]$NoTunnel,
    [int]$EnginePort = 8000,
    [int]$BackendPort = 8080,
    [int]$FrontendPort = 8081
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ComposeFile = Join-Path $Root "docker-compose.yml"

function Start-InWindow {
    param([string]$Title, [string]$WorkDir, [string]$Command)
    $script = "`$host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$WorkDir'; $Command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $script | Out-Null
}

function Stop-Tunnel {
    $found = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $found) {
        Write-Host ("  터널 종료 (PID {0})" -f $p.ProcessId)
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
    }
    # dev.ps1 과 같은 이유 -- 프로세스만 죽이면 담고 있던 -NoExit 창이 빈 채로 남는다.
    $found = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*WindowTitle = 'IDV 터널*" }
    foreach ($w in $found) {
        Write-Host ("  창 닫기 (PID {0})" -f $w.ProcessId)
        try { Stop-Process -Id $w.ProcessId -Force -ErrorAction Stop } catch { }
    }
}

function Wait-Port {
    param([string]$Label, [string]$Url, [int]$Seconds = 180)
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing | Out-Null
            Write-Host ("  {0} 준비됨" -f $Label) -ForegroundColor Green
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    Write-Host ("  {0} 기동 실패 ({1}초 대기)" -f $Label, $Seconds) -ForegroundColor Red
    return $false
}

Write-Host ""
Write-Host "터널 정리" -ForegroundColor Cyan
Stop-Tunnel

if ($Stop) {
    Write-Host ""
    Write-Host "컨테이너 정리 (docker compose down)" -ForegroundColor Cyan
    docker compose -f $ComposeFile down
    Write-Host ""
    Write-Host "종료했습니다." -ForegroundColor Cyan
    Write-Host ""
    exit 0
}

# cloudflared 가 PATH에 없으면 여기서 바로 알려주는 게, 컨테이너까지 다 띄워놓고
# 터널 단계에서야 실패하는 것보다 낫다.
if (-not $NoTunnel -and -not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "  cloudflared 가 안 보입니다. 'winget install --id Cloudflare.cloudflared' 로 설치 후" -ForegroundColor Yellow
    Write-Host "  터미널을 새로 열어서(PATH 반영) 다시 실행하세요. 컨테이너만 먼저 띄우려면 -NoTunnel 을 쓰세요." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# Docker Desktop 자체가 안 켜져 있으면 compose 가 바로 실패한다 -- 에러 메시지만
# 보고 "뭐가 문제지" 헤매지 않도록 먼저 확인한다.
docker info *>$null
if (-not $?) {
    Write-Host ""
    Write-Host "  Docker Desktop이 안 떠 있습니다. 먼저 켜고 완전히 뜰 때까지(고래 아이콘 고정) 기다리세요." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# docker-compose.yml 상단 주석에 적힌 그 문제 -- 로컬 dev.ps1 스택(8000/8080)이나
# 항상 떠 있는 Windows RabbitMQ 서비스(5672)가 포트를 물고 있으면 컨테이너가 못 뜬다.
# 여기서 미리 알려주면, 나중에 "mssql은 됐는데 왜 engine이 안 뜨지"로 헤매지 않는다.
Write-Host ""
Write-Host "포트 확인" -ForegroundColor Cyan
foreach ($p in $EnginePort, $BackendPort, 5672) {
    $busy = Test-NetConnection -ComputerName localhost -Port $p -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($busy) {
        Write-Host ("  {0} 포트가 이미 쓰이고 있습니다 -- 컨테이너가 못 뜰 수 있습니다." -f $p) -ForegroundColor Yellow
    }
}
Write-Host "  (8000/8080이 걸리면 .\dev.ps1 -Stop, 5672가 걸리면 Windows 서비스 목록에서 RabbitMQ를 내리세요.)"

Write-Host ""
Write-Host "컨테이너 기동 (docker compose up --build -d)" -ForegroundColor Cyan
docker compose -f $ComposeFile up --build -d

Write-Host ""
Write-Host "준비 확인" -ForegroundColor Cyan
$engineOk   = Wait-Port -Label "엔진  " -Url "http://localhost:$EnginePort/health"
$backendOk  = Wait-Port -Label "백엔드" -Url "http://localhost:$BackendPort/api/health"
$frontendOk = Wait-Port -Label "화면  " -Url "http://localhost:$FrontendPort/"

if (-not $frontendOk) {
    Write-Host ""
    Write-Host "  화면 컨테이너가 안 떴습니다. 'docker compose logs frontend' 로 확인하세요." -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "  로컬       http://localhost:$FrontendPort" -ForegroundColor Green
if (-not ($engineOk -and $backendOk)) {
    Write-Host "  일부 계층이 아직 안 떴습니다. 'docker compose logs' 로 확인하세요." -ForegroundColor Yellow
}

if ($NoTunnel) {
    Write-Host ""
    Write-Host "  -NoTunnel 이라 터널은 안 엽니다."
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "터널 오픈" -ForegroundColor Cyan
Start-InWindow -Title "IDV 터널" -WorkDir $Root -Command "cloudflared tunnel --url http://localhost:$FrontendPort"

Write-Host ""
Write-Host "  새로 뜬 'IDV 터널' 창에서 https://....trycloudflare.com 주소가 찍히면 그걸 쓰세요." -ForegroundColor Green
Write-Host "  (실행마다 주소가 바뀝니다 -- 이 창을 닫으면 그 순간 주소가 죽습니다.)"
Write-Host ""
Write-Host "  종료: .\demo.ps1 -Stop"
Write-Host ""
