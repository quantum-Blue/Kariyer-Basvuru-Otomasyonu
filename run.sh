#!/usr/bin/env bash
# ============================================================================
# Otonom Kariyer Asistanı — İzole Çalıştırma Betiği (run.sh)
# ROS 2 ve global Python paket çakışmalarını önlemek için PYTHONPATH sıfırlanır.
# Hem Linux/WSL hem de Windows (Git Bash) ortamlarını destekler.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH=""
export PYTHONUTF8=1

if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON_BIN=".venv/Scripts/python.exe"
else
    echo "[HATA] Sanal ortam (.venv) bulunamadı! Lütfen önce 'python -m venv .venv' oluşturun." >&2
    exit 1
fi

exec "$PYTHON_BIN" main.py "$@"

