"""텔레그램 봇으로 알림을 보내고 사용자의 답장(y/n)을 읽는다. .env 미설정 시 조용히 건너뛴다."""

import json
import os
from pathlib import Path

import requests

ENV_PATH = Path(__file__).parent / ".env"
OFFSET_PATH = Path(__file__).parent / "state" / "telegram_offset.json"


def load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")})
    return env


def send_notification(text: str) -> bool:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    resp.raise_for_status()
    return True


def fetch_replies(after_ts: float) -> list[str]:
    """after_ts(unix 초) 이후 사용자가 봇 대화방에 보낸 텍스트 메시지를 시간순으로 반환.

    getUpdates의 offset을 state에 저장해 같은 메시지를 두 번 처리하지 않는다.
    """
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return []
    offset = 0
    if OFFSET_PATH.exists():
        offset = json.loads(OFFSET_PATH.read_text()).get("offset", 0)
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": offset + 1, "timeout": 0},
        timeout=20,
    )
    resp.raise_for_status()
    texts = []
    for update in resp.json().get("result", []):
        offset = max(offset, update["update_id"])
        msg = update.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue
        if msg.get("date", 0) < after_ts:
            continue
        if msg.get("text"):
            texts.append(msg["text"])
    OFFSET_PATH.parent.mkdir(exist_ok=True)
    OFFSET_PATH.write_text(json.dumps({"offset": offset}))
    return texts


def format_video_message(video: dict, app_url: str | None = None) -> str:
    lines = [f"📊 [{video['channel']}] {video['title']}"]
    buys = [op["stock"] for op in video["opinions"] if op["stance"] == "매수"]
    sells = [op["stock"] for op in video["opinions"] if op["stance"] == "매도"]
    holds = [op["stock"] for op in video["opinions"] if op["stance"] in ("보유", "관망")]
    if buys:
        lines.append(f"🟢 매수: {', '.join(buys)}")
    if sells:
        lines.append(f"🔴 매도: {', '.join(sells)}")
    if holds:
        lines.append(f"⚪ 보유/관망: {', '.join(holds)}")
    if not video["opinions"]:
        lines.append("종목 의견 없음 (시황 위주)")
    if app_url:
        lines.append(f"상세: {app_url}")
    return "\n".join(lines)
