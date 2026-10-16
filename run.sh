#!/bin/bash
# OMR 가채점 프로그램 실행 (Linux/Mac)
cd "$(dirname "$0")"
if [ ! -d "venv" ]; then
    echo "가상환경 생성 중..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi
./venv/bin/streamlit run app.py
