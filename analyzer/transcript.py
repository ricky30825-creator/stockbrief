"""유튜브 영상 자막을 추출한다."""

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
)

MAX_CHARS = 60_000


def get_transcript(video_id: str) -> str | None:
    """자막 전문을 반환. 자막이 없으면 None (그 외 오류는 예외 전파)."""
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en"])
    except CouldNotRetrieveTranscript:
        return None
    text = " ".join(snippet.text.strip() for snippet in fetched)
    return truncate_evenly(text, MAX_CHARS)


def truncate_evenly(text: str, max_chars: int) -> str:
    """라이브 다시보기 등 초장문 자막은 앞/중/뒤 3구간을 균등 샘플링해 절단한다."""
    if len(text) <= max_chars:
        return text
    part = max_chars // 3
    mid_start = (len(text) - part) // 2
    return "\n[...중략...]\n".join(
        [text[:part], text[mid_start : mid_start + part], text[-part:]]
    )
