#!/bin/bash
# 도우미 사이드카. 죽으면 10초 뒤 다시 띄우고, 이미 떠 있으면 바로 끝난다(flock).
#   시작: tmux new -d -s askhowto /home/n20/.local/share/atlauncher/workspace/askhowto/run.sh
#   로그: logs/worker.log (질문/답변 전체 기록은 logs/qa.jsonl)

cd "$(dirname "$0")"
mkdir -p logs
exec 9>logs/worker.lock
flock -n 9 || { echo "이미 실행 중"; exit 0; }

while true; do
  # LLM 주소·모델·API 키 (재시작할 때마다 다시 읽어서 이 파일만 고치고 사이드카를 재시작하면 바뀐다)
  [ -f llm.env ] && . ./llm.env
  python3 -m askhowto serve 2>&1 | tee -a logs/worker.log
  echo "$(date '+%F %T') worker exited, restarting in 10s" | tee -a logs/worker.log
  sleep 10
done
