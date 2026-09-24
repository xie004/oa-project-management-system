$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Venv = Join-Path $Backend ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Uvicorn = Join-Path $Venv "Scripts\uvicorn.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating Python virtual environment..."
    py -3 -m venv $Venv
}

Write-Host "Installing backend dependencies..."
& $Python -m pip install -r (Join-Path $Backend "requirements.txt")

if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location $Frontend
    npm install
    Pop-Location
}

Write-Host "Building frontend..."
Push-Location $Frontend
npm run build
Pop-Location

$primaryNetwork = Get-NetIPConfiguration |
    Where-Object { $_.IPv4Address -and $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    Select-Object -First 1

if ($primaryNetwork) {
    $ip = $primaryNetwork.IPv4Address.IPAddress
} else {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -First 1 -ExpandProperty IPAddress)
}

if (-not $ip) {
    $ip = "127.0.0.1"
}

$Port = 8010
$PortInUse = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.State -in @("Listen", "Bound") }
if ($PortInUse) {
    $Port = 8012
    $FallbackInUse = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -in @("Listen", "Bound") }
    if ($FallbackInUse) { throw "端口 8010 和 8012 都已被占用，请先关闭冲突程序。" }
    Write-Host "端口 8010 已被其他程序占用，使用备用端口 8012。"
}

Write-Host ""
Write-Host "国产化OA集成项目管理系统已启动："
Write-Host "  本机访问：http://127.0.0.1:$Port"
Write-Host "  局域网访问：http://$ip`:$Port"
Write-Host ""

Push-Location $Backend
& $Uvicorn app.main:app --host 0.0.0.0 --port $Port
Pop-Location
