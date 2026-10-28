#!/bin/bash
# 자동 감시만 해제합니다. 전사본·원본·설정 파일은 지우지 않아요.
set -euo pipefail
LABEL="com.lecture-transcribe.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "자동 전사를 해제했어요. ~/강의전사 폴더는 그대로 남아 있어요."
