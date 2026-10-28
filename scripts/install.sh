#!/bin/bash
# 강의 자동 전사 설치 스크립트 (애플 실리콘 맥 전용)
# 사용법: 저장소 최상위 폴더에서  bash scripts/install.sh
set -euo pipefail

ROOT="$HOME/강의전사"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC_PY="$REPO/src/kkamji/transcribe.py"
SRC_CFG="$REPO/config/default.json"
SUBJECTS=(운영체제 시스템보안 알고리즘 데이터베이스)
LABEL="com.lecture-transcribe.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf '\n==> %s\n' "$1"; }

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "애플 실리콘 맥(M1 이상)에서만 동작해요." >&2
  exit 1
fi

if pgrep -f "$ROOT/transcribe.py" >/dev/null 2>&1; then
  echo "지금 전사가 진행 중이에요. 다시 설치하면 진행 중인 전사가 중단되니," >&2
  echo "완료 알림이 뜬 뒤에 다시 실행하세요. (진행 상황: tail ~/강의전사/로그/transcribe.log)" >&2
  exit 1
fi

say "Homebrew 확인"
if ! command -v brew >/dev/null 2>&1; then
  for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [[ -x "$p" ]] && eval "$("$p" shellenv)" && break
  done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew가 없어요. https://brew.sh 의 설치 명령을 먼저 실행한 뒤 다시 실행하세요." >&2
  exit 1
fi
BREW_PREFIX="$(brew --prefix)"

say "ffmpeg, Python 3.12 설치 확인"
command -v ffmpeg >/dev/null 2>&1 || brew install ffmpeg
[[ -x "$BREW_PREFIX/bin/python3.12" ]] || brew install python@3.12
PY="$BREW_PREFIX/bin/python3.12"

say "폴더 만들기: $ROOT"
for s in "${SUBJECTS[@]}"; do
  mkdir -p "$ROOT/넣는곳/$s" "$ROOT/전사본/$s" "$ROOT/원본/$s"
done
mkdir -p "$ROOT/로그"

cp "$SRC_PY" "$ROOT/transcribe.py"
if [[ -f "$ROOT/config.json" ]]; then
  # 기존 설정(내가 고친 프롬프트 등)은 유지하고, 새로 생긴 항목만 추가
  "$PY" - "$ROOT/config.json" "$SRC_CFG" <<'PYEOF'
import json, sys
cur_path, new_path = sys.argv[1], sys.argv[2]
cur = json.load(open(cur_path, encoding="utf-8"))
new = json.load(open(new_path, encoding="utf-8"))
added = [k for k in new if k not in cur]
for k in added:
    cur[k] = new[k]
# 이전 v2 기본값(잡음 제거 포함)을 그대로 쓰고 있었다면 새 기본값으로 교체
OLD_V2_FILTER = "highpass=f=80,lowpass=f=7600,afftdn=nf=-25,dynaudnorm=f=150:g=15"
# 이전 기본 교정 규칙을 고치지 않고 쓰고 있었다면 과목별로 새 규칙으로 교체
OLD_TERM_FIXES = json.loads('{"_공통": {}, "운영체제": {"인트럭트|인트럼트|인트럽트|인트로트|인트럼프|인턱트|인터럽프": "인터럽트", "(?<![A-Za-z])(?:DNA|DLA|DMG|dna|dla|dmg|dma)(?![A-Za-z])": "DMA", "디바이스 드라이(?:브|더)": "디바이스 드라이버", "(?<![가-힣])(?:시피유|씨피유|시피오)(?![가-힣]{2})": "CPU"}, "시스템보안": {"버퍼 ?오버 ?플로우?": "버퍼 오버플로"}, "알고리즘": {"빅 ?오 ?표기": "빅오 표기"}, "데이터베이스": {"에스큐엘": "SQL"}}')
for subj, rules in new.get("term_fixes", {}).items():
    cur_tf = cur.setdefault("term_fixes", {})
    if subj not in cur_tf or cur_tf[subj] == OLD_TERM_FIXES.get(subj):
        if cur_tf.get(subj) != rules:
            cur_tf[subj] = rules
            added.append(f"term_fixes/{subj}")
if cur.get("preprocess_filter") == OLD_V2_FILTER:
    cur["preprocess_filter"] = new["preprocess_filter"]
    added.append("preprocess_filter(잡음 제거 제외로 변경)")
for subj, prompt in new.get("prompts", {}).items():
    cur.setdefault("prompts", {}).setdefault(subj, prompt)
json.dump(cur, open(cur_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("기존 config.json 유지" + (f", 새 항목 추가: {', '.join(added)}" if added else ""))
PYEOF
else
  cp "$SRC_CFG" "$ROOT/config.json"
fi

say "Python 가상환경과 mlx-whisper 설치"
[[ -x "$ROOT/.venv/bin/python" ]] || "$PY" -m venv "$ROOT/.venv"
if "$ROOT/.venv/bin/python" -c "import mlx_whisper" 2>/dev/null; then
  echo "이미 설치돼 있어 건너뜀 (업데이트하려면: bash install.sh --upgrade)"
  if [[ "${1:-}" == "--upgrade" ]]; then
    "$ROOT/.venv/bin/pip" install --upgrade mlx-whisper
  fi
else
  "$ROOT/.venv/bin/pip" install --upgrade pip >/dev/null
  "$ROOT/.venv/bin/pip" install mlx-whisper
fi

say "Whisper 모델 확인 (처음 한 번만 약 1.5GB 다운로드)"
MODEL="$("$ROOT/.venv/bin/python" -c "import json;print(json.load(open('$ROOT/config.json'))['model'])")"
if "$ROOT/.venv/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL', local_files_only=True)" >/dev/null 2>&1; then
  echo "모델이 이미 있어 건너뜀: $MODEL"
else
  "$ROOT/.venv/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL')"
fi

say "폴더 감시 등록 (launchd)"
mkdir -p "$HOME/Library/LaunchAgents"
{
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string>
    <string>$ROOT/transcribe.py</string>
  </array>
  <key>WatchPaths</key>
  <array>
    <string>$ROOT/넣는곳</string>
EOF
  for s in "${SUBJECTS[@]}"; do
    echo "    <string>$ROOT/넣는곳/$s</string>"
  done
  cat <<EOF
  </array>
  <key>StartInterval</key>
  <integer>900</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>ProcessType</key>
  <string>Standard</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$BREW_PREFIX/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key>
    <string>ko_KR.UTF-8</string>
    <key>PYTHONIOENCODING</key>
    <string>utf-8</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$ROOT/로그/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/로그/launchd.log</string>
</dict>
</plist>
EOF
} > "$PLIST"
plutil -lint "$PLIST" >/dev/null

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

say "설치 완료"
echo "녹음 파일을 $ROOT/넣는곳/<과목>/ 에 넣으면 자동으로 전사돼요."
echo "전사본: $ROOT/전사본/<과목>/   로그: $ROOT/로그/transcribe.log"
open "$ROOT" 2>/dev/null || true
