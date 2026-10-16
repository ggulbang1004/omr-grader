@echo off
REM OMR 가채점 프로그램 실행 (Windows)
cd /d "%~dp0"
if not exist "venv" (
    echo 가상환경 생성 중...
    python -m venv venv
    venv\Scripts\pip install -r requirements.txt
)
venv\Scripts\streamlit run app.py
pause
