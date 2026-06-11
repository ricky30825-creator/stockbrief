"""유튜브 채널의 영상 목록을 가져온다 (API 키 불필요).

1순위: 채널 RSS 피드 (정확한 게시 시각 제공)
2순위: RSS가 404 등으로 죽으면 채널 /videos 페이지의 ytInitialData를 파싱
       (게시 시각은 "3시간 전" 같은 상대 표기를 근사 변환)
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
VIDEOS_URL = "https://www.youtube.com/channel/{channel_id}/videos"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
MAX_VIDEOS = 15  # RSS와 동일한 개수 유지

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def fetch_channel_videos(channel_id: str, timeout: int = 20) -> list[dict]:
    """채널의 최신 영상 목록을 최신순으로 반환한다."""
    try:
        resp = requests.get(FEED_URL.format(channel_id=channel_id), timeout=timeout)
        resp.raise_for_status()
        return parse_feed(resp.text)
    except requests.RequestException:
        return scrape_channel_videos(channel_id, timeout=timeout)


def parse_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    videos = []
    for entry in root.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=NS)
        if not video_id:
            continue
        videos.append(
            {
                "video_id": video_id,
                "title": entry.findtext("atom:title", default="", namespaces=NS),
                "published": entry.findtext("atom:published", default="", namespaces=NS),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    return videos


# --- 채널 페이지 파싱 폴백 ---


def scrape_channel_videos(channel_id: str, timeout: int = 20) -> list[dict]:
    resp = requests.get(
        VIDEOS_URL.format(channel_id=channel_id),
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "ko-KR,ko;q=0.9"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = _extract_yt_initial_data(resp.text)
    return parse_channel_page(data)


def _extract_yt_initial_data(html: str) -> dict:
    marker = html.find("ytInitialData")
    if marker == -1:
        raise ValueError("ytInitialData 없음")
    start = html.find("{", marker)
    obj, _ = json.JSONDecoder().raw_decode(html[start:])
    return obj


def parse_channel_page(data: dict, now: datetime | None = None) -> list[dict]:
    """ytInitialData에서 영상 목록 추출 (신/구 두 가지 렌더러 형식 지원)."""
    now = now or datetime.now(timezone.utc)
    videos, seen = [], set()
    for d in _iter_dicts(data):
        item = None
        if "lockupViewModel" in d:
            lv = d["lockupViewModel"]
            vid = lv.get("contentId")
            if vid:
                texts = list(_iter_texts(lv))
                title = _first_title(lv) or (texts[0] if texts else "")
                item = (vid, title, _find_relative_time(texts))
        elif "videoRenderer" in d:
            vr = d["videoRenderer"]
            vid = vr.get("videoId")
            if vid:
                title = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", []))
                rel = vr.get("publishedTimeText", {}).get("simpleText", "")
                item = (vid, title, rel)
        if not item or item[0] in seen:
            continue
        vid, title, rel = item
        seen.add(vid)
        videos.append(
            {
                "video_id": vid,
                "title": title,
                "published": parse_relative_time(rel, now).isoformat(),
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
        )
        if len(videos) >= MAX_VIDEOS:
            break
    return videos


def _iter_dicts(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def _iter_texts(node):
    for d in _iter_dicts(node):
        content = d.get("content")
        if isinstance(content, str):
            yield content
        text = d.get("text")
        if isinstance(text, str):
            yield text
        simple = d.get("simpleText")
        if isinstance(simple, str):
            yield simple


def _first_title(lockup: dict) -> str | None:
    title = (
        lockup.get("metadata", {})
        .get("lockupMetadataViewModel", {})
        .get("title", {})
        .get("content")
    )
    return title if isinstance(title, str) else None


_REL_UNITS = {
    "분": 60, "시간": 3600, "일": 86400, "주": 604800,
    "개월": 2592000, "년": 31536000,
    "minute": 60, "hour": 3600, "day": 86400, "week": 604800,
    "month": 2592000, "year": 31536000,
}
_REL_RE = re.compile(
    r"(\d+)\s*(분|시간|일|주|개월|년|minute|hour|day|week|month|year)s?\s*(전|ago)"
)


def _find_relative_time(texts: list[str]) -> str:
    for t in texts:
        if _REL_RE.search(t):
            return t
    return ""


def parse_relative_time(text: str, now: datetime) -> datetime:
    """'3시간 전' 같은 상대 시각을 근사 절대 시각으로 변환. 파싱 불가 시 now."""
    m = _REL_RE.search(text or "")
    if not m:
        return now
    return now - timedelta(seconds=int(m.group(1)) * _REL_UNITS[m.group(2)])
