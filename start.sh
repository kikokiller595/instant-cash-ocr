#!/bin/sh
set -eu

DATA_DIR="${DATA_DIR:-/app/site}"
LOG_FILE="${DATA_DIR}/schedule.log"
PID_FILE="${DATA_DIR}/scheduler.pid"

mkdir -p "${DATA_DIR}"
touch "${LOG_FILE}"

flag="$(printf '%s' "${RUN_OCR_SCHEDULER:-0}" | tr '[:upper:]' '[:lower:]')"
if [ "${flag}" = "1" ] || [ "${flag}" = "true" ] || [ "${flag}" = "yes" ] || [ "${flag}" = "on" ]; then
  python /app/scheduler.py &
  echo "$!" > "${PID_FILE}"
fi

exec python /app/states_controller.py
