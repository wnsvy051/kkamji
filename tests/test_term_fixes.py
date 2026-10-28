import pytest

OS = "운영체제"


@pytest.mark.parametrize("before, after", [
    # 조사까지 맞춰서 교정
    ("장치가 인트럼터를 발생시킨다", "장치가 인터럽트를 발생시킨다"),
    ("키보드가 인트럭을 보내면", "키보드가 인터럽트를 보내면"),
    ("인트럼이 들어오면", "인터럽트가 들어오면"),
    ("인트랩이 걸리면", "인터럽트가 걸리면"),
    ("인트럭이라는 것은", "인터럽트라는 것은"),
    ("인트럭입니다", "인터럽트입니다"),
    ("타이머 인트로트에 의해서", "타이머 인터럽트에 의해서"),
    # 방식 이름
    ("트럭 드리븐 아이유 방식", "인터럽트 드리븐 I/O 방식"),
    ("프로그래드 아이유 방식", "프로그램드 I/O 방식"),
    ("아이유를 처리하는 방법", "I/O를 처리하는 방법"),
    # 장치·약어
    ("DNA 컨트롤러가 메모리에 쓴다", "DMA 컨트롤러가 메모리에 쓴다"),
    ("DLA 전송이 끝나면", "DMA 전송이 끝나면"),
    ("디바이스 드라이브를 로드한다", "디바이스 드라이버를 로드한다"),
    ("시피오가 명령을 실행한다", "CPU가 명령을 실행한다"),
])
def test_os_misrecognitions_fixed(fix, before, after):
    assert fix(OS, before) == after


@pytest.mark.parametrize("line", [
    "트럭이 지나간다",
    "디스크 드라이브에서 읽어옵니다",
    "인터넷에 연결하면",
    "아이오 장치",
])
def test_os_unrelated_words_untouched(fix, line):
    assert fix(OS, line) == line


def test_rules_only_apply_to_their_subject(fix):
    assert fix("데이터베이스", "DNA 장치") == "DNA 장치"
    assert fix("데이터베이스", "에스큐엘 문장") == "SQL 문장"


def test_disabled(tr, default_cfg):
    cfg = dict(default_cfg, term_fixes_enabled=False)
    assert tr.build_term_fixes(cfg, OS) == []
