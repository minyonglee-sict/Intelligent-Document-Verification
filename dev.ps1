<#
.SYNOPSIS
    개발용 3개 프로세스를 한 번에 띄운다 (Python 엔진 / Java 백엔드 / React 화면).

.DESCRIPTION
    React 스택은 프로세스 셋이 함께 떠 있어야 동작한다. 손으로 띄우면 하나를 빠뜨리거나,
    코드를 고치고 그 계층만 재시작하는 실수가 난다 -- Java 를 재시작하지 않아 새로 만든
    경로가 404 로 나오는 일이 실제로 있었다. 그래서 항상 셋을 함께 다시 세운다.

    엔진은 개발용 DB 를 본다. Streamlit(main.py)은 원본 DB 를 그대로 쓰므로 이 스크립트와
    무관하게 계속 돌아도 된다.

.EXAMPLE
    .\dev.ps1              # 셋 다 재시작
    .\dev.ps1 -Stop        # 셋 다 종료
    .\dev.ps1 -Database DocumentVerification    # 원본 DB 로 띄우기(주의)
#>
param(
    [switch]$Stop,
    [string]$Database = "DocumentVerification_Dev",
    [int]$EnginePort = 8000,
    [int]$BackendPort = 8080,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# 우리가 띄운 것만 골라 잡는다. 명령줄로 가려내야 다른 python/java/node 를 건드리지 않는다.
$Signatures = @(
    @{ Name = "엔진";   Process = "python"; Match = "uvicorn api:app" },
    @{ Name = "백엔드"; Process = "java";   Match = "com.idv.backend" },
    @{ Name = "화면";   Process = "node";   Match = "vite" }
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

Start-InWindow -Title "IDV 엔진 ($EnginePort)" -WorkDir $Root -Command @"
`$env:MSSQL_DATABASE = '$Database'
`$env:PYTHONIOENCODING = 'utf-8'
python -m uvicorn api:app --port $EnginePort
"@

Start-InWindow -Title "IDV 백엔드 ($BackendPort)" -WorkDir (Join-Path $Root "backend") -Command @"
.\mvnw.cmd -B spring-boot:run
"@

Start-InWindow -Title "IDV 화면 ($FrontendPort)" -WorkDir (Join-Path $Root "frontend") -Command @"
npm run dev
"@

Write-Host ""
Write-Host "준비 확인" -ForegroundColor Cyan
$engineOk   = Wait-Port -Label "엔진  " -Url "http://127.0.0.1:$EnginePort/health"
$backendOk  = Wait-Port -Label "백엔드" -Url "http://127.0.0.1:$BackendPort/api/documents/counts"
# Vite 는 IPv6(::1)에만 붙어서 127.0.0.1 로는 응답하지 않는다. localhost 로 확인한다.
$frontendOk = Wait-Port -Label "화면  " -Url "http://localhost:$FrontendPort/"

Write-Host ""
if ($engineOk -and $backendOk -and $frontendOk) {
    Write-Host "  화면      http://localhost:$FrontendPort" -ForegroundColor Green
    Write-Host "  백엔드    http://127.0.0.1:$BackendPort/api"
    Write-Host "  엔진      http://127.0.0.1:$EnginePort/docs   (OpenAPI 문서)"
    Write-Host ""
    Write-Host "  브라우저는 반드시 localhost 로 여세요. 127.0.0.1 은 Vite 가 받지 않습니다."
} else {
    Write-Host "  일부가 뜨지 않았습니다. 각 창의 로그를 확인하세요." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  종료: .\dev.ps1 -Stop"
Write-Host ""
