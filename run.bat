@echo off
REM Starts the Spark service on port 9180 (syncs .venv with uv on each run). The first start downloads the weights (about 4 GB).
REM LABEL: Spark Service
REM DESCRIPTION: Shared HTTP service that runs Spark-X2.6-1.7B on the CPU for every project.

cd /d "%~dp0"

python -m uv sync
if errorlevel 1 (
    echo uv sync failed. Install uv: winget install astral-sh.uv
    pause
    exit /b 1
)
call .venv\Scripts\activate

python src/app.py
pause
