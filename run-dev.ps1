#Requires -Version 7
<#
.SYNOPSIS
    Developer experience script: start Postgres in Docker, apply migrations,
    then launch backend + frontend with hot-reload.
#>

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition

cd $projectRoot

Write-Host "== TubeLess Dev Environment ==" -ForegroundColor Cyan

# 1. Start Postgres via Docker
Write-Host "`n[1/4] Starting Postgres..." -ForegroundColor Green
$pgContainer = docker ps --filter "name=ytless-postgres" --format "{{.Names}}"
if (-not $pgContainer) {
    docker run -d --name ytless-postgres `
        -e POSTGRES_DB=tubeless `
        -e POSTGRES_USER=postgres `
        -e POSTGRES_PASSWORD=postgres `
        -p 5432:5432 `
        -v ytless_pgdata:/var/lib/postgresql/data `
        ankane/pgvector:latest 2>$null
    Start-Sleep -Seconds 3
} else {
    Write-Host "    Postgres container already running."
    $pgDatabase = docker inspect $pgContainer --format "{{range .Config.Env}}{{println .}}{{end}}" |
        Select-String "^POSTGRES_DB="
    if ($pgDatabase -and $pgDatabase.Line -ne "POSTGRES_DB=tubeless") {
        Write-Warning "The existing $pgContainer container uses $($pgDatabase.Line). The local setup expects POSTGRES_DB=tubeless. Recreate that container if migrations fail."
    }
}

# This script runs the backend and frontend on the host. Keep all host-facing
# URLs explicit so a Docker-oriented .env cannot make the local proxy point at
# the Docker service name.
$env:TUBELESS_RUNTIME = "local"
$env:INTERNAL_BACKEND_URL = "http://127.0.0.1:8000"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000"
$env:NEXT_PUBLIC_WS_URL = "ws://127.0.0.1:8000"
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/tubeless"
$env:DATABASE_URL_SYNC = "postgresql://postgres:postgres@127.0.0.1:5432/tubeless"

# 2. Install / update backend deps
Write-Host "`n[2/4] Checking backend dependencies..." -ForegroundColor Green
$poetry = (Get-Command poetry -ErrorAction SilentlyContinue).Source
if (-not $poetry) { $poetry = "python -m poetry" }
cd backend
& $poetry install --no-root 2>$null | Out-Null
cd ..

# 3. Apply migrations
Write-Host "`n[3/4] Applying database migrations..." -ForegroundColor Green
cd backend
& $poetry run alembic upgrade head
cd ..

# 4. Seed test data (optional, idempotent)
$seedScript = Join-Path backend scripts seed_test_data.py
if (Test-Path $seedScript) {
    Write-Host "`n[3.5/4] Seeding test data..." -ForegroundColor Yellow
    cd backend
    & $poetry run python scripts/seed_test_data.py
    cd ..
}

# 5. Start both services concurrently
Write-Host "`n[4/4] Starting services with hot-reload..." -ForegroundColor Green
Write-Host "    Backend: http://localhost:8000"
Write-Host "    Frontend: http://localhost:3000"
Write-Host "    API Docs: http://localhost:8000/docs`n"

$backendCmd = "cd $projectRoot\backend; & `"$poetry`" run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
$frontendCmd = "cd $projectRoot\frontend; npm run dev"

Start-Process pwsh -ArgumentList "-Command", $backendCmd -WindowStyle Normal
Start-Process pwsh -ArgumentList "-Command", $frontendCmd -WindowStyle Normal

Write-Host "Both servers started! Press Ctrl+C here does NOT stop them." -ForegroundColor Cyan
Write-Host "Use docker stop ytless-postgres to shutdown Postgres.`n"
