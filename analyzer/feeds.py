"""유튜브 채널 RSS 피드에서 영상 목록을 가져온다 (API 키 불필요)."""

import xml.etree.ElementTree as ET

import requests

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def fetch_channel_videos(channel_id: str, timeout: int = 20) -> list[dict]:
    """채널의 최신 영상 목록(최대 15개)을 최신순으로 반환한다."""
    resp = requests.get(FEED_URL.format(channel_id=channel_id), timeout=timeout)
    resp.raise_for_status()
    return parse_feed(resp.text)


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
