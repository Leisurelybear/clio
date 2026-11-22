#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Virtual environment not found. Run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Starting web UI..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m clio serve @args
