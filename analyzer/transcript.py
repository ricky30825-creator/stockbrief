"""유튜브 영상 자막을 추출한다.

'진짜 자막 없음'(영구)과 'IP 차단·일시 오류'(재시도 대상)를 구분하는 것이 중요하다:
연속 요청 시 유튜브가 IP를 차단하는데, 이를 자막 없음으로 기록하면 영상을 영영 놓친다.

기본 경로는 youtube-transcript-api이고, IP 차단 등 일시 오류 시 yt-dlp로 폴백한다.
yt-dlp는 다른 클라이언트를 흉내내고 우회 기능이 많아 한쪽이 막혀도 받아질 때가 있다.
"""

import glob
import json
import os
import random
import subprocess
import sys
import tempfile
import time

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import _errors as yt_errors

MAX_CHARS = 60_000
# 연속 요청으로 인한 IP 차단 예방. 가정용 IP에서는 간격을 충분히 두고 랜덤화하면
# 프록시 없이도 대부분 차단을 피한다 (일정 간격은 봇 패턴으로 인식되기 쉬움).
FETCH_DELAY_RANGE = (20, 40)
YTDLP_TIMEOUT = 120

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
        return None  # 영상에 자막 자체가 없음 → yt-dlp로도 못 받으므로 폴백 안 함
    except Exception as primary:  # IpBlocked, RequestBlocked, 네트워크 오류 등 → yt-dlp 폴백
        text = _fetch_via_ytdlp(video_id)
        if text:
            return truncate_evenly(text, MAX_CHARS)
        raise TransientFetchError(type(primary).__name__) from primary
    text = " ".join(snippet.text.strip() for snippet in fetched)
    return truncate_evenly(text, MAX_CHARS)


def _fetch_via_ytdlp(video_id: str) -> str | None:
    """yt-dlp로 자막(수동 우선, 없으면 자동생성)을 받아 텍스트로 반환. 실패 시 None."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "sub")
        try:
            subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--skip-download",
                 "--write-subs", "--write-auto-subs",
                 "--sub-langs", "ko,en", "--sub-format", "json3",
                 "--impersonate", "chrome",  # curl_cffi로 브라우저 TLS 지문 흉내 → 차단 우회력↑
                 "-o", out, f"https://www.youtube.com/watch?v={video_id}"],
                capture_output=True, text=True, timeout=YTDLP_TIMEOUT,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        # 한국어 우선, 없으면 영어, 그래도 없으면 받아진 아무 json3
        files = (glob.glob(f"{out}*.ko*.json3") or glob.glob(f"{out}*.en*.json3")
                 or glob.glob(f"{out}*.json3"))
        if not files:
            return None
        try:
            return parse_json3(open(files[0], encoding="utf-8").read())
        except (OSError, ValueError):
            return None


def parse_json3(raw: str) -> str:
    """yt-dlp json3 자막을 평문으로 변환한다."""
    data = json.loads(raw)
    parts = []
    for event in data.get("events", []):
        for seg in event.get("segs") or []:
            t = seg.get("utf8", "")
            if t and t != "\n":
                parts.append(t)
    return " ".join("".join(parts).split())


def truncate_evenly(text: str, max_chars: int) -> str:
    """라이브 다시보기 등 초장문 자막은 앞/중/뒤 3구간을 균등 샘플링해 절단한다."""
    if len(text) <= max_chars:
        return text
    part = max_chars // 3
    mid_start = (len(text) - part) // 2
    return "\n[...중략...]\n".join(
        [text[:part], text[mid_start : mid_start + part], text[-part:]]
    )
