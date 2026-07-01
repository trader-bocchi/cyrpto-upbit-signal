#!/usr/bin/env bash
# 로컬 cron용 실행 스크립트 — 업비트 4h 캔들 마감 직후 시그널 발송.
# crontab 예시(KST, 캔들 마감 5분 뒤):
#   5 1,5,9,13,17,21 * * * /Users/jongwon/proj/application/cyrpto-upbit-signal/scripts/run_local.sh
set -uo pipefail

PROJECT="/Users/jongwon/proj/application/cyrpto-upbit-signal"
PYTHON="/Users/jongwon/opt/anaconda3/bin/python3"

cd "$PROJECT" || exit 1

mkdir -p logs
LOG="logs/cron_$(date +%Y%m%d).log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') 실행 시작 =====" >> "$LOG"
# .env(텔레그램 토큰)는 config.py의 load_dotenv가 CWD에서 읽음
"$PYTHON" batch/main.py >> "$LOG" 2>&1
echo "===== 종료 (exit=$?) =====" >> "$LOG"
