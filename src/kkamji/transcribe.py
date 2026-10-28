#!/usr/bin/env python3
"""강의 녹음 자동 전사 (v2).

~/강의전사/넣는곳/<과목>/ 에 녹음 파일을 넣으면
~/강의전사/전사본/<과목>/ 에 txt 전사본을 만들고,
원본은 ~/강의전사/원본/<과목>/ 으로 옮긴다.

launchd가 폴더 변화를 감지하면 이 스크립트를 실행한다.
수동 실행: ~/강의전사/.venv/bin/python ~/강의전사/transcribe.py

v2 변경점
- 진행률을 로그에 기록 (5% 단위, 남은 시간 추정)
- 전사 중에는 맥 자동 잠자기 방지 (caffeinate)
- 음성 전처리: 16kHz 모노 + 저주파 잡음 제거 + 작은 소리 키우기
- 재시도 단계 축소 (잡음 많은 녹음에서 속도 개선)
- 한 줄 안의 "아 아 아 아 ..." 같은 반복 정리
- 배터리일 때 건너뛰기 옵션 (기본 꺼짐)
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("LECTURE_ROOT", Path(__file__).resolve().parent))
CONFIG_PATH = ROOT / "config.json"
INBOX = ROOT / "넣는곳"
OUT = ROOT / "전사본"
DONE = ROOT / "원본"
FAILED = ROOT / "실패"
LOG_DIR = ROOT / "로그"
TMP_DIR = ROOT / ".tmp"
LOCK_PATH = ROOT / ".transcribe.lock"

AUDIO_EXT = {
    ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".amr", ".3gp", ".mp4", ".mov", ".m4v", ".webm", ".mkv",
}

DEFAULT_CONFIG = {
    "model": "mlx-community/whisper-large-v3-turbo",
    "timestamps": True,
    "hallucination_silence_threshold": 2.0,
    "temperature": [0.0, 0.4, 0.8],
    "preprocess": True,
    # 잡음 제거(afftdn 등)는 Whisper 정확도를 오히려 떨어뜨린다는 연구가 있어 기본에서 뺌.
    # 저주파 웅웅거림 컷 + 작은 구간만 키우는 음량 보정만 사용.
    "preprocess_filter": "highpass=f=80,lowpass=f=7600,dynaudnorm=f=150:g=15",
    "term_fixes_enabled": True,
    "term_fixes": {},
    "prevent_sleep": True,
    "ac_power_only": False,
    "notify": True,
    "stable_wait_sec": 5,
    "prompts": {},
}

# 무음·잡음 구간에서 Whisper가 지어내는 흔한 문구.
# 세그먼트 전체가 이 문구뿐일 때만 지운다 (실제 강의 문장은 건드리지 않음).
HALLUCINATION_PATTERNS = [
    r"시청(해\s*)?주셔서\s*감사(합니다|드립니다)",
    r"구독(과|\s*,)?\s*좋아요",
    r"좋아요(와|\s*,)?\s*구독",
    r"알림\s*설정(까지)?",
    r"부탁(드립니다|드려요|해요|합니다)",
    r"다음\s*(영상|시간)에서\s*(만나요|뵙겠습니다)",
    r"(MBC|KBS|SBS|YTN|JTBC)\s*뉴스",
    r"자막\s*(제공|제작)",
    r"(한글\s*)?자막\s*by",
    r"감사합니다\.?",
    # 녹음 첫머리 무음에서 나오는 상담원·비서 말투 환각
    r"안녕하세요",
    r"무엇을\s*도와\s*드릴까요\??",
    r"제가\s*도와\s*드릴[게께]요",
    r"(어떻게|무엇을)\s*도와\s*드릴까요\??",
]
_HALLU_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(HALLUCINATION_PATTERNS) + r")[\s.!~,]*)+\s*$"
)
# 같은 짧은 토큰이 4번 이상 연달아 나오면 한 번만 남김 ("아 아 아 아", "5 5 5 5")
_INLINE_REPEAT_RE = re.compile(r"(?<!\S)(\S{1,6})(?:\s+\1){3,}(?!\S)")
# 정리 후 "아", "으 어" 같은 감탄사만 남은 줄은 버림
_FILLER_ONLY_RE = re.compile(r"^(?:(?:아|어|으|음|흠|에|오|우)[\s.,…~!?]*)+$")


def nfc(s: str) -> str:
    """macOS 파일명(NFD)과 설정 파일(NFC)의 한글 비교를 맞춘다."""
    return unicodedata.normalize("NFC", s)


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "transcribe.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["prompts"] = {nfc(k): v for k, v in cfg.get("prompts", {}).items()}
    return cfg


def notify(cfg: dict, title: str, msg: str) -> None:
    if not cfg.get("notify", True):
        return
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{esc(msg)}" with title "{esc(title)}"'],
            check=False, capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def on_battery() -> bool:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                             text=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "Battery Power" in out


def keep_awake() -> None:
    """이 스크립트가 살아 있는 동안 맥 자동 잠자기를 막는다 (덮개 닫기는 못 막음)."""
    try:
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def is_stable(path: Path, wait: float) -> bool:
    """복사 중인 파일을 건드리지 않도록 크기가 멈췄는지 확인."""
    try:
        s1 = path.stat().st_size
        time.sleep(wait)
        s2 = path.stat().st_size
    except FileNotFoundError:
        return False
    return s1 > 0 and s1 == s2


def candidates():
    if not INBOX.exists():
        return []
    found = []
    for subj_dir in sorted(INBOX.iterdir()):
        if not subj_dir.is_dir() or subj_dir.name.startswith("."):
            continue
        for f in sorted(subj_dir.iterdir()):
            if (f.is_file() and not f.name.startswith(".")
                    and f.suffix.lower() in AUDIO_EXT):
                found.append((nfc(subj_dir.name), f))
    return found


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def collapse_inline(text: str) -> str:
    prev = None
    while prev != text:  # "아 아 아 아 으 으 으 으" 처럼 여러 덩어리도 처리
        prev = text
        text = _INLINE_REPEAT_RE.sub(r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def clean_segments(segments):
    """환각 문구만 있는 세그먼트 제거 + 반복 정리."""
    out = []
    removed = 0
    prev_text = None
    repeat = 0
    for seg in segments:
        text = collapse_inline((seg.get("text") or "").strip())
        if not text or _HALLU_RE.match(text) or _FILLER_ONLY_RE.match(text):
            removed += 1
            continue
        if text == prev_text:
            repeat += 1
            if repeat >= 2:  # 같은 문장은 최대 2번까지만 남김
                removed += 1
                continue
        else:
            repeat = 0
        prev_text = text
        out.append({"start": float(seg.get("start", 0.0)), "text": text})
    return out, removed


def build_term_fixes(cfg: dict, subject: str):
    """config의 "_공통" + 과목별 교정 목록을 (정규식, 바꿀 말) 목록으로."""
    if not cfg.get("term_fixes_enabled", True):
        return []
    table = {nfc(k): v for k, v in (cfg.get("term_fixes") or {}).items()}
    rules = []
    for key in ("_공통", subject):
        for pattern, repl in (table.get(key) or {}).items():
            try:
                rules.append((re.compile(pattern), repl))
            except re.error as e:
                log(f"교정 규칙 무시 ({key}: {pattern}): {e}")
    return rules


def apply_term_fixes(segments, rules):
    count = 0
    for seg in segments:
        for rx, repl in rules:
            seg["text"], n = rx.subn(repl, seg["text"])
            count += n
    return count


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        p = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not p.exists():
            return p
        n += 1


def preprocess(src: Path, cfg: dict) -> Path:
    """ffmpeg로 16kHz 모노 wav + 잡음 제거·음량 보정. 실패하면 원본을 그대로 쓴다."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    dst = TMP_DIR / f"{os.getpid()}.wav"
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000"]
    if cfg.get("preprocess_filter"):
        cmd += ["-af", cfg["preprocess_filter"]]
    cmd.append(str(dst))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        log(f"전처리 실패, 원본으로 진행: {r.stderr.strip()[:300]}")
        return src
    return dst


class _ProgressLogger:
    """mlx_whisper 내부 tqdm 자리에 끼워 넣어 진행률을 로그로 남긴다."""

    def __init__(self, total=None, unit=None, disable=None, **_):
        self.total = total or 1
        self.n = 0
        self.t0 = time.time()
        self.next_pct = 5

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, k=1):
        self.n += k
        pct = self.n * 100 // self.total
        if pct >= self.next_pct and pct < 100:
            el = time.time() - self.t0
            remain = el * (self.total - self.n) / max(self.n, 1)
            log(f"  진행 {pct}% · 경과 {el/60:.1f}분 · 남은 시간 약 {remain/60:.0f}분")
            self.next_pct = (pct // 5 + 1) * 5


def install_progress_hook() -> None:
    mod = sys.modules.get("mlx_whisper.transcribe")
    if mod is not None and hasattr(mod, "tqdm"):
        class _Shim:
            tqdm = _ProgressLogger
        mod.tqdm = _Shim


def transcribe_file(subject: str, src: Path, cfg: dict) -> Path:
    import mlx_whisper  # 무거운 import는 실제로 처리할 파일이 있을 때만
    install_progress_hook()

    audio = preprocess(src, cfg) if cfg.get("preprocess", True) else src

    kwargs = dict(
        path_or_hf_repo=cfg["model"],
        language="ko",
        initial_prompt=cfg["prompts"].get(subject),
        condition_on_previous_text=False,
        temperature=tuple(cfg.get("temperature") or (0.0,)),
        verbose=False,  # 진행률 훅이 동작하도록
    )
    thr = cfg.get("hallucination_silence_threshold")
    if thr:
        kwargs["word_timestamps"] = True
        kwargs["hallucination_silence_threshold"] = float(thr)

    t0 = time.time()
    try:
        result = mlx_whisper.transcribe(str(audio), **kwargs)
    finally:
        if audio != src:
            audio.unlink(missing_ok=True)
    elapsed = time.time() - t0

    segments, removed = clean_segments(result.get("segments", []))
    fixed = apply_term_fixes(segments, build_term_fixes(cfg, subject))
    duration = max((s.get("end", 0.0) for s in result.get("segments", [])),
                   default=0.0)

    rec_date = datetime.fromtimestamp(src.stat().st_mtime)
    out_dir = OUT / subject
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = unique_path(out_dir / f"{rec_date:%Y-%m-%d}_{src.stem}.txt")

    lines = [
        f"# {subject} 강의 전사본",
        f"# 원본: {src.name} | 녹음일: {rec_date:%Y-%m-%d} | "
        f"길이: {fmt_ts(duration)} | 모델: {cfg['model'].split('/')[-1]}"
        f"{' | 전처리' if audio != src else ''}",
        "# 자동 전사본이라 전공 용어·숫자·기호가 틀릴 수 있음",
        "",
    ]
    if cfg.get("timestamps", True):
        lines += [f"[{fmt_ts(s['start'])}] {s['text']}" for s in segments]
    else:
        lines += [s["text"] for s in segments]

    tmp = out_path.with_suffix(".txt.part")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.rename(out_path)

    log(f"완료: {subject}/{src.name} -> {out_path.name} "
        f"(음성 {fmt_ts(duration)}, 처리 {elapsed/60:.1f}분, "
        f"세그먼트 {len(segments)}개, 환각·반복 제거 {removed}개, 용어 교정 {fixed}곳)")
    return out_path


def move_to(base: Path, subject: str, src: Path) -> Path:
    dest_dir = base / subject
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest_dir / src.name)
    shutil.move(str(src), str(dest))
    return dest


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    lock_f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # 이미 다른 실행이 처리 중

    cfg = load_config()
    if not candidates():
        return 0
    if cfg.get("ac_power_only") and on_battery():
        log("배터리 사용 중이라 건너뜀 (전원 연결 후 15분 안에 자동 처리)")
        return 0
    if cfg.get("prevent_sleep", True):
        keep_awake()

    wait = float(cfg.get("stable_wait_sec", 5))
    idle_rounds = 0

    while True:
        items = candidates()
        if not items:
            break
        progressed = False
        for subject, src in items:
            if not is_stable(src, wait):
                continue
            log(f"시작: {subject}/{src.name}")
            notify(cfg, "강의 전사 시작", f"{subject} · {src.name}")
            try:
                out_path = transcribe_file(subject, src, cfg)
                move_to(DONE, subject, src)
                notify(cfg, "강의 전사 완료", f"{subject} · {out_path.name}")
            except Exception:
                log(f"실패: {subject}/{src.name}\n{traceback.format_exc()}")
                try:
                    move_to(FAILED, subject, src)
                except Exception:
                    pass
                notify(cfg, "강의 전사 실패",
                       f"{subject} · {src.name} (로그 확인)")
            progressed = True
            break  # 처리 도중 새로 들어온 파일까지 다시 스캔
        if progressed:
            idle_rounds = 0
            continue
        # 남은 파일이 모두 복사 중이면 잠깐 기다렸다 재확인 (최대 약 3분)
        idle_rounds += 1
        if idle_rounds > 12:
            for subject, src in items:
                try:
                    empty = src.stat().st_size == 0
                except FileNotFoundError:
                    continue
                if empty:
                    log(f"빈 파일이라 건너뜀: {subject}/{src.name}")
                    move_to(FAILED, subject, src)
            log("복사가 끝나지 않은 파일은 다음 실행으로 미룸")
            break
        time.sleep(10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
