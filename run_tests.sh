#!/usr/bin/env bash
# ============================================================================
# Otonom Kariyer Asistanı — İzole Test Koşucu Betiği (run_tests.sh)
# ROS 2 (ament, launch_testing vb.) pytest eklenti çakışmalarını engeller.
# Hem Linux/WSL hem de Windows (Git Bash) ortamlarını destekler.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH=""
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=0
export PYTHONUTF8=1

if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON_BIN=".venv/Scripts/python.exe"
else
    echo "[HATA] Sanal ortam (.venv) bulunamadı! Lütfen önce 'python -m venv .venv' oluşturun." >&2
    exit 1
fi

exec "$PYTHON_BIN" -m pytest \
    -p no:ament_flake8 \
    -p no:ament_lint \
    -p no:ament_pep257 \
    -p no:ament_xmllint \
    -p no:ament_copyright \
    -p no:launch_testing \
    -p no:launch_testing_ros \
    "$@"
