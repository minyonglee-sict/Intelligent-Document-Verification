<#
.SYNOPSIS
    개발용 프로세스를 한 번에 띄운다 (Python 엔진 / Java 백엔드 / React 화면 / 문서 처리 워커).

.DESCRIPTION
    React 스택은 프로세스들이 함께 떠 있어야 동작한다. 손으로 띄우면 하나를 빠뜨리거나,
    코드를 고치고 그 계층만 재시작하는 실수가 난다 -- Java 를 재시작하지 않아 새로 만든
    경로가 404 로 나오는 일이 실제로 있었다. 그래서 항상 다 함께 다시 세운다.

    이제 Streamlit·HTTP API·MCP 가 모두 같은 DB(config 기본값)를 본다. 화면마다 다른
    DB를 보면 같은 문서가 다르게 보이기 때문이다. -Database 는 그 기본값을 덮어쓴다.

    문서 업로드 처리는 RabbitMQ 큐를 거친다(worker.py). RabbitMQ 자체는 이 스크립트가
    띄우지 않는다 -- Windows 서비스로 따로 설치되어 항상 떠 있는 것을 전제로 한다.
    RabbitMQ가 안 떠 있으면 엔진·백엔드·화면은 정상 기동해도 업로드만 503으로 실패한다.

.EXAMPLE
    .\dev.ps1              # 다 재시작 (워커 1개 포함)
    .\dev.ps1 -Stop        # 다 종료
    .\dev.ps1 -Workers 3   # 문서 처리 워커를 3개 띄워서 병렬로 처리
    .\dev.ps1 -Database DocumentVerification    # 이관 전 원본 DB 로 띄우기(읽기 전용으로만)
#>
param(
    [switch]$Stop,
    [string]$Database = "DocumentVerification_Dev",
    [int]$EnginePort = 8000,
    [int]$BackendPort = 8080,
    [int]$FrontendPort = 5173,
    [int]$Workers = 1
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# 우리가 띄운 것만 골라 잡는다. 명령줄로 가려내야 다른 python/java/node 를 건드리지 않는다.
$Signatures = @(
    @{ Name = "엔진";   Process = "python"; Match = "uvicorn api:app" },
    @{ Name = "백엔드"; Process = "java";   Match = "com.idv.backend" },
    @{ Name = "화면";   Process = "node";   Match = "vite" },
    @{ Name = "워커";   Process = "python"; Match = "worker.py" }
)

function Stop-Idv {
    foreach ($sig in $Signatures) {
        $found = Get-CimInstance Win32_Process -Filter "Name='$($sig.Process).exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$($sig.Match)*" }
        foreach ($p in $found) {
            Write-Host ("  {0} 종료 (PID {1})" -f $sig.Name, $p.ProcessId)
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { }
        }
    }

    # 안의 프로세스만 죽이면, 그걸 담고 있던 -NoExit 창은 안 닫힌 채로 남는다
    # (-NoExit 은 명령이 끝나거나 죽어도 창을 계속 띄워 둔다). 이걸 안 하면 dev.ps1
    # 을 돌릴 때마다 빈 창이 3개씩 쌓인다.
    #
    # MainWindowTitle 로 찾으려 했으나 이 환경(Windows Terminal)에서는 항상 비어
    # 있었다 -- Windows Terminal 이 콘솔 창을 대신 소유해서, 그 밑의 powershell.exe
    # 프로세스 자체는 창 핸들이 없다. 대신 우리가 실행할 때 넘긴 명령줄
    # ("WindowTitle = 'IDV ...'")은 그대로 남으므로 그걸로 찾는다.
    $found = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*WindowTitle = 'IDV *" }
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

# 각 서비스는 자기 창에서 돈다. 로그가 그대로 보이고, 창을 닫으면 그 서비스만 멈춘다.
function Start-InWindow {
    param([string]$Title, [string]$WorkDir, [string]$Command)
    $script = "`$host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$WorkDir'; $Command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $script | Out-Null
}

Write-Host ""
Write-Host "실행 중인 개발 프로세스 정리" -ForegroundColor Cyan
Stop-Idv
Start-Sleep -Seconds 2

if ($Stop) {
    Write-Host ""
    Write-Host "종료했습니다. Streamlit(8501)은 건드리지 않았습니다." -ForegroundColor Cyan
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "기동" -ForegroundColor Cyan
Write-Host ("  DB: {0}" -f $Database)

# RabbitMQ는 이 스크립트가 안 띄운다 -- Windows 서비스로 따로 켜져 있어야 한다.
# 여기서 미리 확인해두면, 나중에 업로드가 503 나고서야 "브로커가 안 떠 있었네"를
# 깨닫는 대신 기동 시점에 바로 알 수 있다.
$rabbitOk = (Test-NetConnection -ComputerName localhost -Port 5672 -WarningAction SilentlyContinue -InformationLevel Quiet)
if ($rabbitOk) {
    Write-Host "  RabbitMQ: 떠 있음 (5672)" -ForegroundColor Green
} else {
    Write-Host "  RabbitMQ: 안 떠 있음 -- 업로드가 503으로 실패합니다. 서비스를 먼저 켜세요." -ForegroundColor Yellow
}

# SMTP 계정은 이 파일에 못 박아두지 않는다 -- dev.ps1은 git에 커밋되는 스크립트라,
# 여기 비밀번호를 적으면 그대로 저장소에 남는다. 대신 .gitignore 된
# .smtp_credentials(1줄: 계정, 2줄: 앱 비밀번호)를 실행할 때마다 읽어온다.
# 파일이 없으면(아직 설정 전) 빈 값으로 두고, 엔진은 그 상태로도 뜬다 -- 재설정
# 요청 메일만 못 보낼 뿐 나머지는 정상 동작해야 한다.
$SmtpUser = ""
$SmtpPassword = ""
$SmtpCredFile = Join-Path $Root ".smtp_credentials"
if (Test-Path $SmtpCredFile) {
    $lines = Get-Content $SmtpCredFile
    if ($lines.Count -ge 2) {
        $SmtpUser = $lines[0].Trim()
        $SmtpPassword = $lines[1].Trim()
    }
}

Start-InWindow -Title "IDV 엔진 ($EnginePort)" -WorkDir $Root -Command @"
`$env:MSSQL_DATABASE = '$Database'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:SMTP_USER = '$SmtpUser'
`$env:SMTP_PASSWORD = '$SmtpPassword'
python -m uvicorn api:app --port $EnginePort
"@

Start-InWindow -Title "IDV 백엔드 ($BackendPort)" -WorkDir (Join-Path $Root "backend") -Command @"
.\mvnw.cmd -B spring-boot:run
"@

Start-InWindow -Title "IDV 화면 ($FrontendPort)" -WorkDir (Join-Path $Root "frontend") -Command @"
npm run dev
"@

# 워커는 HTTP 서버가 아니라 큐를 듣기만 하는 프로세스라, 몇 개를 띄우든 다 같은
# 큐(document_jobs)를 나눠 받는다 -- 창마다 제목만 번호로 구분한다.
for ($w = 1; $w -le $Workers; $w++) {
    Start-InWindow -Title "IDV 워커 $w" -WorkDir $Root -Command @"
`$env:MSSQL_DATABASE = '$Database'
`$env:PYTHONIOENCODING = 'utf-8'
python worker.py
"@
}

Write-Host ""
Write-Host "준비 확인" -ForegroundColor Cyan
$engineOk   = Wait-Port -Label "엔진  " -Url "http://127.0.0.1:$EnginePort/health"

# /api/documents/counts 는 이제 로그인이 필요해 401을 준다 -- Invoke-WebRequest 는
# 그걸 에러로 본다. /api/health 는 로그인 없이도 열려 있는 유일한 경로라 여기로 확인한다.
$backendOk  = Wait-Port -Label "백엔드" -Url "http://127.0.0.1:$BackendPort/api/health"
# Vite 는 IPv6(::1)에만 붙어서 127.0.0.1 로는 응답하지 않는다. localhost 로 확인한다.
$frontendOk = Wait-Port -Label "화면  " -Url "http://localhost:$FrontendPort/"

Write-Host ""
if ($engineOk -and $backendOk -and $frontendOk) {
    Write-Host "  화면      http://localhost:$FrontendPort" -ForegroundColor Green
    Write-Host "  백엔드    http://127.0.0.1:$BackendPort/api"
    Write-Host "  엔진      http://127.0.0.1:$EnginePort/docs   (OpenAPI 문서)"
    Write-Host ("  워커      {0}개 (자체 창에서 로그 확인, HTTP 포트 없음)" -f $Workers)
    Write-Host ""
    Write-Host "  브라우저는 반드시 localhost 로 여세요. 127.0.0.1 은 Vite 가 받지 않습니다."
    if (-not $rabbitOk) {
        Write-Host "  RabbitMQ가 안 떠 있어서 업로드는 여전히 실패합니다." -ForegroundColor Yellow
    }
} else {
    Write-Host "  일부가 뜨지 않았습니다. 각 창의 로그를 확인하세요." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  종료: .\dev.ps1 -Stop"
Write-Host ""
