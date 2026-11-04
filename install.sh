#!/bin/bash
# 문서를 다시 만들고 KubeJS 스크립트를 서버에 복사한다.
# 적용: 서버 재시작(/reload 금지 — 이 기기에서는 워치독이 서버를 죽인다) 후, 사이드카가 안 떠 있으면 run.sh 실행.

set -e
cd "$(dirname "$0")"
SERVER_DIR=${ASKHOWTO_SERVER_DIR:-../../servers/SocietySunlitValley415A1201}

python3 -m askhowto build
# JsonIO.write는 폴더를 만들지 않아서 미리 만들어 둔다.
mkdir -p "$SERVER_DIR/local/askhowto/requests" "$SERVER_DIR/local/askhowto/responses" "$SERVER_DIR/local/askhowto/feedback"
cp kubejs/askhowto.js kubejs/askhowto_recipes.js kubejs/askhowto_prices.js "$SERVER_DIR/kubejs/server_scripts/"
echo "복사함: $SERVER_DIR/kubejs/server_scripts/askhowto.js, askhowto_recipes.js, askhowto_prices.js"
echo "다음: 서버 재시작(/reload 금지), 그리고 tmux new -d -s askhowto $PWD/run.sh"
