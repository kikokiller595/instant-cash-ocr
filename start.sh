#!/bin/sh
set -eu

DATA_DIR="${DATA_DIR:-/app/site}"
LOG_FILE="${DATA_DIR}/schedule.log"

mkdir -p "${DATA_DIR}"
touch "${LOG_FILE}"

exec python /app/states_controller.py
