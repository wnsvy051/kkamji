import json
import os
import tempfile
from pathlib import Path

import pytest

# 테스트 중 로그·임시 파일이 저장소 안에 생기지 않도록 import 전에 작업 폴더를 지정
os.environ.setdefault("LECTURE_ROOT", tempfile.mkdtemp(prefix="kkamji-test-"))

from kkamji import transcribe as t  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def tr():
    return t


@pytest.fixture(scope="session")
def default_cfg():
    with open(REPO / "config" / "default.json", encoding="utf-8") as f:
        cfg = dict(t.DEFAULT_CONFIG)
        cfg.update(json.load(f))
    return cfg


@pytest.fixture
def fix(tr, default_cfg):
    """fix("운영체제", "인트럭을 걸어주면") -> "인터럽트를 걸어주면" """
    def _fix(subject, text):
        segs = [{"text": text}]
        tr.apply_term_fixes(segs, tr.build_term_fixes(default_cfg, subject))
        return segs[0]["text"]
    return _fix
