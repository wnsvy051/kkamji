import pytest


def texts(tr, lines):
    out, _ = tr.clean_segments([{"text": s, "start": i} for i, s in enumerate(lines)])
    return [s["text"] for s in out]


@pytest.mark.parametrize("line", [
    "시청해주셔서 감사합니다.",
    "구독과 좋아요 부탁드립니다!",
    "좋아요와 구독, 알림 설정까지 부탁드립니다.",
    "감사합니다",
    "안녕하세요. 무엇을 도와드릴까요?",
    "제가 도와드릴께요.",
    "아 아 아",
    "으 으 으 으 으 으",
])
def test_hallucination_and_filler_lines_removed(tr, line):
    assert texts(tr, [line]) == []


@pytest.mark.parametrize("line", [
    "MBC 뉴스 김철수입니다.",
    "감사합니다 다음 시간에는 페이징을 합니다",
    "구독 기반 모델은 좋아요",
    "세마포어를 부탁드립니다라고 하면",
    "안녕하세요 오늘은 입출력을 하겠습니다",
    "오늘 수업 감사합니다",
    "네 맞습니다",
])
def test_real_lecture_lines_kept(tr, line):
    assert texts(tr, [line]) == [line]


def test_inline_repeats_collapsed(tr):
    assert texts(tr, ["아 아 아 아 아 아 그래서 DMA가 옮깁니다"]) == ["아 그래서 DMA가 옮깁니다"]
    assert texts(tr, ["5 5 5 5 4 4 4 4 디바이스 드라이버"]) == ["5 4 디바이스 드라이버"]


def test_consecutive_duplicate_lines_capped_at_two(tr):
    lines = ["문맥 교환이 일어납니다."] * 4 + ["다음 내용"]
    assert texts(tr, lines) == ["문맥 교환이 일어납니다."] * 2 + ["다음 내용"]
