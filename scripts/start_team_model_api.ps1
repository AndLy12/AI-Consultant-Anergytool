# Временный доступ команды: секретные файлы остаются в work, в Git идёт код.
param(
    [string]$PythonExe,
    [string]$PrivateDirectory,
    [ValidateSet('ssh','cloudflare')][string]$Transport = 'ssh',
    [switch]$Stop,
    [switch]$RotateKey
)
$ErrorActionPreference = 'Stop'
$qaRepo = Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) { $PythonExe = Join-Path $qaRepo '.venv/Scripts/python.exe' }
if (-not $PrivateDirectory) { $PrivateDirectory = Join-Path $qaRepo 'work/team-model-api' }
if ((Split-Path -Leaf $PrivateDirectory) -ne 'team-model-api') { throw 'Нужен отдельный каталог team-model-api' }
New-Item -ItemType Directory -Path $PrivateDirectory -Force | Out-Null
$qaPrivate = (Resolve-Path -LiteralPath $PrivateDirectory).Path
$qaConfigPath = Join-Path $qaPrivate 'gateway-config.json'
$qaStatePath = Join-Path $qaPrivate 'process-state.json'
$qaConnectionPath = Join-Path $qaPrivate 'TEAM_API_CONNECTION.txt'

function Stop-QaOwnedProcess([int]$qaProcessId) {
    $qaProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$qaProcessId" -ErrorAction SilentlyContinue
    if ($qaProcess -and $qaProcess.CommandLine -and $qaProcess.CommandLine.Contains($qaPrivate)) {
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$qaProcessId" | ForEach-Object {
            Stop-QaOwnedProcess ([int]$_.ProcessId)
        }
        Stop-Process -Id $qaProcessId -Force -ErrorAction SilentlyContinue
    }
}

$qaState = if (Test-Path -LiteralPath $qaStatePath) { Get-Content -LiteralPath $qaStatePath -Raw | ConvertFrom-Json } else { $null }
if ($Stop -or $RotateKey) {
    if ($qaState) {
        Stop-QaOwnedProcess ([int]$qaState.tunnel_pid)
        Stop-QaOwnedProcess ([int]$qaState.gateway_pid)
    }
    if ($Stop) { Write-Output 'Доступ команды остановлен; LM Studio продолжает работать.'; return }
}
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Укажите Python проекта через -PythonExe' }

# Меняем ACL только выделенной папки, не родительского проекта/профиля.
$qaAccount = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $qaPrivate '/inheritance:r' '/grant:r' "${qaAccount}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить доступ к папке ключа' }
if (-not (Test-Path -LiteralPath $qaConfigPath) -or $RotateKey) {
    @'
import json, secrets, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'token': secrets.token_urlsafe(36)}, indent=2), encoding='utf-8')
'@ | & $PythonExe -X utf8 - $qaConfigPath
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать закрытый ключ' }
}
$qaConfig = Get-Content -LiteralPath $qaConfigPath -Raw | ConvertFrom-Json
if ($qaState -and -not $RotateKey) {
    try {
        $qaHealthy = Invoke-RestMethod -Uri 'http://127.0.0.1:1235/health' -Headers @{Authorization=('Bearer ' + $qaConfig.token)} -TimeoutSec 3
        $qaTunnelAlive = Get-CimInstance Win32_Process -Filter "ProcessId=$($qaState.tunnel_pid)"
        if ($qaHealthy.status -eq 'running' -and $qaTunnelAlive -and $qaTunnelAlive.CommandLine.Contains($qaPrivate)) {
            # Живой SSH-процесс не гарантирует, что сервис сохранил внешний туннель.
            $qaExistingExternal = Invoke-RestMethod -Uri ($qaState.base_url + '/models') -Headers @{Authorization=('Bearer ' + $qaConfig.token)} -TimeoutSec 15
            if ($qaExistingExternal.data[0].id -ne 'energytool-eval') { throw 'Внешний туннель недоступен' }
            [ordered]@{base_url=$qaState.base_url;model='energytool-eval';stream=$false;max_tokens=1200;parallel=1;status='running';token_location='Передаётся Даньком отдельно'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaRepo 'docs/team-6v/TEAM_MODEL_ENDPOINT.json') -Encoding utf8
            Write-Output "API уже запущен: $($qaState.base_url)"
            Write-Output "Закрытые параметры: $qaConnectionPath"
            return
        }
    } catch { }
    Stop-QaOwnedProcess ([int]$qaState.tunnel_pid)
    Stop-QaOwnedProcess ([int]$qaState.gateway_pid)
}

$qaBinary = Join-Path $qaPrivate 'cloudflared.exe'
$qaReleasePath = Join-Path $qaPrivate 'cloudflared-release.json'
if ($Transport -eq 'cloudflare') {
if (-not (Test-Path -LiteralPath $qaBinary) -or -not (Test-Path -LiteralPath $qaReleasePath)) {
    $qaRelease = Invoke-RestMethod 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest' -Headers @{'User-Agent'='Energytool-team-model'}
    $qaAsset = $qaRelease.assets | Where-Object { $_.name -eq 'cloudflared-windows-amd64.exe' }
    if (-not $qaAsset.digest.StartsWith('sha256:')) { throw 'Нет официального SHA256 cloudflared' }
    [pscustomobject]@{Version=$qaRelease.tag_name;URL=$qaAsset.browser_download_url;Digest=$qaAsset.digest} | ConvertTo-Json | Set-Content -LiteralPath $qaReleasePath -Encoding utf8
    Invoke-WebRequest -Uri $qaAsset.browser_download_url -OutFile $qaBinary
}
$qaRelease = Get-Content -LiteralPath $qaReleasePath -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $qaBinary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $qaRelease.Digest.Substring(7)) { throw 'Неверный SHA256 cloudflared' }
}

$qaStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$qaGateway = Start-Process -FilePath $PythonExe -ArgumentList @('-X','utf8',('"' + (Join-Path $PSScriptRoot 'team_model_gateway.py') + '"'),'--config',('"' + $qaConfigPath + '"')) -WindowStyle Hidden -PassThru -WorkingDirectory $qaRepo -RedirectStandardOutput (Join-Path $qaPrivate "gateway-$qaStamp.out.log") -RedirectStandardError (Join-Path $qaPrivate "gateway-$qaStamp.err.log")
$qaState = [ordered]@{gateway_pid=$qaGateway.Id;tunnel_pid=0;base_url=$null;started_at=(Get-Date).ToUniversalTime().ToString('o')}
$qaState | ConvertTo-Json | Set-Content -LiteralPath $qaStatePath -Encoding utf8
$qaReady = $false
for ($qaTry=0; $qaTry -lt 40; $qaTry++) {
    try {
        $qaHealth = Invoke-RestMethod 'http://127.0.0.1:1235/health' -Headers @{Authorization=('Bearer ' + $qaConfig.token)} -TimeoutSec 1
        if ($qaHealth.status -eq 'running') { $qaReady=$true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $qaReady) { throw "Шлюз не запустился: проверьте gateway-$qaStamp.err.log" }
$qaTunnelLog = Join-Path $qaPrivate "tunnel-$qaStamp.log"
if ($Transport -eq 'ssh') {
    # Отдельный known_hosts не затрагивает пользовательские SSH-настройки.
    # accept-new сохраняет первый ключ и запрещает незаметную замену ключа.
    # nokey не передаёт сервису существующие пользовательские SSH-идентичности.
    $qaSsh = (Get-Command ssh -ErrorAction Stop).Source
    $qaSshArguments = @('-T','-o','BatchMode=yes','-o','IdentityFile=none','-o','IdentitiesOnly=yes','-o','PreferredAuthentications=none','-o','StrictHostKeyChecking=accept-new','-o',('UserKnownHostsFile="' + (Join-Path $qaPrivate 'localhostrun-known-hosts') + '"'),'-o','ConnectTimeout=10','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','ExitOnForwardFailure=yes','-R','80:127.0.0.1:1235','nokey@localhost.run')
    $qaTunnel = Start-Process -FilePath $qaSsh -ArgumentList $qaSshArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $qaTunnelLog -RedirectStandardError (Join-Path $qaPrivate "tunnel-$qaStamp.err.log")
} else {
$qaTunnel = Start-Process -FilePath $qaBinary -ArgumentList @('tunnel','--url','http://127.0.0.1:1235','--protocol','http2','--no-autoupdate','--logfile',('"' + $qaTunnelLog + '"')) -WindowStyle Hidden -PassThru -WorkingDirectory $qaPrivate -RedirectStandardOutput (Join-Path $qaPrivate "tunnel-$qaStamp.out.log") -RedirectStandardError (Join-Path $qaPrivate "tunnel-$qaStamp.err.log")
}
$qaState.tunnel_pid = $qaTunnel.Id
$qaState['tunnel_kind'] = $Transport
$qaState | ConvertTo-Json | Set-Content -LiteralPath $qaStatePath -Encoding utf8
$qaUrl = $null
for ($qaTry=0; $qaTry -lt 60; $qaTry++) {
    if (Test-Path -LiteralPath $qaTunnelLog) {
        # Сразу после запуска файл уже существует, но SSH ещё мог ничего не записать.
        $qaTunnelText = [string](Get-Content -LiteralPath $qaTunnelLog -Raw)
        $qaPattern = if ($Transport -eq 'ssh') { 'https://[a-z0-9.-]+\.lhr\.life' } else { 'https://[a-z0-9-]+\.trycloudflare\.com' }
        $qaMatch = [regex]::Match($qaTunnelText, $qaPattern)
        if ($Transport -eq 'cloudflare' -and $qaTunnelText -notmatch 'Registered tunnel connection') { Start-Sleep -Milliseconds 500; continue }
        if ($qaMatch.Success) { $qaUrl=$qaMatch.Value; break }
    }
    Start-Sleep -Milliseconds 500
}
if (-not $qaUrl) { throw "Нет URL туннеля: проверьте $qaTunnelLog" }
# Сам выданный URL не доказывает работоспособность внешнего соединения.
$qaExternal = Invoke-RestMethod -Uri ($qaUrl + '/v1/models') -Headers @{Authorization=('Bearer ' + $qaConfig.token)} -TimeoutSec 15
if ($qaExternal.data[0].id -ne 'energytool-eval') { throw 'Внешний адрес не прошёл проверку' }
$qaState.base_url = $qaUrl + '/v1'
$qaState | ConvertTo-Json | Set-Content -LiteralPath $qaStatePath -Encoding utf8
@"
Закрытые параметры команды. Передавать только участникам проекта, не публиковать.
LLM_BASE_URL=$($qaState.base_url)
LLM_API_KEY=$($qaConfig.token)
LLM_MODEL=energytool-eval
TEMPERATURE=0
MAX_TOKENS=1200
MAX_WORKERS=1
stream=false
Компьютер, LM Studio, шлюз и tunnel должны оставаться включёнными.
"@ | Set-Content -LiteralPath $qaConnectionPath -Encoding utf8
$qaPublic = [ordered]@{base_url=$qaState.base_url;model='energytool-eval';stream=$false;max_tokens=1200;parallel=1;status='running';updated_at=$qaState.started_at;token_location='Передаётся Даньком отдельно'}
$qaPublic | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaRepo 'docs/team-6v/TEAM_MODEL_ENDPOINT.json') -Encoding utf8
Write-Output "API: $($qaState.base_url)"
Write-Output "Ключ и параметры: $qaConnectionPath"
