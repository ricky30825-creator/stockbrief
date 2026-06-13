"""유튜브 영상 자막을 추출한다.

'진짜 자막 없음'(영구)과 'IP 차단·일시 오류'(재시도 대상)를 구분하는 것이 중요하다:
연속 요청 시 유튜브가 IP를 차단하는데, 이를 자막 없음으로 기록하면 영상을 영영 놓친다.
"""

import random
import time

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import _errors as yt_errors

MAX_CHARS = 60_000
# 연속 요청으로 인한 IP 차단 예방. 가정용 IP에서는 간격을 충분히 두고 랜덤화하면
# 프록시 없이도 대부분 차단을 피한다 (일정 간격은 봇 패턴으로 인식되기 쉬움).
FETCH_DELAY_RANGE = (20, 40)

# 영상 자체에 자막이 없거나 접근 불가 → 재시도해도 소용없는 영구 사유
_PERMANENT = tuple(
    getattr(yt_errors, name)
    for name in ("TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable", "AgeRestricted", "InvalidVideoId")
    if hasattr(yt_errors, name)
)


class TransientFetchError(Exception):
    """IP 차단 등 일시 오류. 다음 주기에 재시도해야 한다."""


def get_transcript(video_id: str) -> str | None:
    """자막 전문을 반환. 영구적으로 자막이 없으면 None, 일시 오류면 TransientFetchError."""
    time.sleep(random.uniform(*FETCH_DELAY_RANGE))
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en"])
    except _PERMANENT:
        return None
    except Exception as e:  # IpBlocked, RequestBlocked, YouTubeRequestFailed, 네트워크 오류 등
        raise TransientFetchError(f"{type(e).__name__}") from e
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
