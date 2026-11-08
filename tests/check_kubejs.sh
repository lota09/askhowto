#!/bin/bash
# KubeJS 스크립트 문법 검사: 서버 mods의 Rhino(KubeJS가 쓰는 것과 같은 파서)로 파싱만 한다. 서버를 켜지 않고 확인용.
cd "$(dirname "$0")/.."
RHINO=$(ls ../../servers/SocietySunlitValley415A1201/mods/rhino-forge-*.jar | head -1)
JDK=${ASKHOWTO_JDK_BIN:-$HOME/.local/jdk/jdk-17.0.21+9/bin}
[ -x "$JDK/javac" ] || JDK=/usr/lib/jvm/java-17-openjdk-arm64/bin
OUT=runs/rhino-check
mkdir -p $OUT
[ $OUT/Check.class -nt tests/rhino/Check.java ] || $JDK/javac -cp "$RHINO" -d $OUT tests/rhino/Check.java
$JDK/java -cp "$RHINO:$OUT" Check kubejs/*.js
