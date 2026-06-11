"""전체 파이프라인: 새 영상 감지 → 자막 → 분석 → 데이터 갱신 → push → 알림.

여러 번 실행해도 안전하다(멱등): 처리한 video_id는 state/processed.json에 기록된다.
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from aggregate import build_stocks
from analyze import analyze_transcript
from feeds import fetch_channel_videos
from notify import format_video_message, load_env, send_notification
from transcript import TransientFetchError, get_transcript

BASE = Path(__file__).parent
REPO = BASE.parent
DATA_DIR = REPO / "docs" / "data"
STATE_PATH = BASE / "state" / "processed.json"
LAST_RUN_PATH = BASE / "state" / "last_run.txt"
CHANNELS_PATH = BASE / "channels.json"
LOG_DIR = BASE / "logs"

MAX_VIDEOS_KEPT = 200      # videos.json에 보관할 최대 영상 수
FIRST_RUN_PER_CHANNEL = 2  # 최초 실행 시 채널당 분석할 최신 영상 수 (백필 폭주 방지)
NO_TRANSCRIPT_GRACE_HOURS = 6  # 이 시간 안 된 새 영상은 자막 생성을 기다리며 재시도
MAX_PER_RUN = 30           # 한 주기 처리 상한 (IP 차단·구독 사용량 폭주 방지)
RUN_AFTER_HOUR = 7         # 이 시각(로컬) 이후, 하루 한 번만 실행

log = logging.getLogger("stockbrief")


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "analyzer.log", encoding="utf-8"),
        ],
    )


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def collect_new_videos(channels: list[dict], processed: set[str]) -> list[dict]:
    """모든 채널에서 미처리 영상을 모은다 (채널 정보 포함, 오래된 것부터)."""
    first_run = not processed
    new_videos = []
    for ch in channels:
        try:
            feed = fetch_channel_videos(ch["channel_id"])
        except Exception as e:
            log.warning("피드 조회 실패 %s: %s", ch["name"], e)
            continue
        fresh = [v for v in feed if v["video_id"] not in processed]
        if first_run:
            fresh = fresh[:FIRST_RUN_PER_CHANNEL]
        for v in fresh:
            v["channel"] = ch["name"]
            v["channel_id"] = ch["channel_id"]
        new_videos.extend(fresh)
    new_videos.sort(key=lambda v: v["published"], reverse=True)
    return new_videos[:MAX_PER_RUN]  # 최신 우선, 나머지 백필은 다음 주기로


def should_run_daily(now: datetime, last_run_date: str) -> bool:
    """오전 RUN_AFTER_HOUR시 이후이고 오늘 아직 안 돌았을 때만 True (하루 1회)."""
    if now.hour < RUN_AFTER_HOUR:
        return False
    return last_run_date != now.strftime("%Y-%m-%d")


def transcript_wait_expired(published_iso: str, now: datetime | None = None) -> bool:
    """업로드 후 충분히 지나 자막 생성을 더 기다릴 필요가 없으면 True."""
    now = now or datetime.now(timezone.utc)
    try:
        published = datetime.fromisoformat(published_iso)
    except ValueError:
        return True
    return (now - published).total_seconds() > NO_TRANSCRIPT_GRACE_HOURS * 3600


def process_video(video: dict) -> dict | None:
    """영상 1개 처리. 반환값 None이면 일시 오류 → 이번 회차는 건너뛰고 다음에 재시도."""
    try:
        text = get_transcript(video["video_id"])
    except TransientFetchError as e:
        log.warning("자막 조회 일시 오류(재시도 예정) %s: %s", video["video_id"], e)
        return None
    record = {k: video[k] for k in ("video_id", "channel", "channel_id", "title", "published", "url")}
    if text is None:
        # 갓 올라온 영상은 자동 자막이 아직 안 생겼을 수 있다 → 다음 주기에 재시도
        if not transcript_wait_expired(video["published"]):
            log.info("자막 대기(재시도 예정): [%s] %s", video["channel"], video["title"])
            return None
        record.update(status="no_transcript", summary="", opinions=[])
        log.info("자막 없음: [%s] %s", video["channel"], video["title"])
        return record
    try:
        result = analyze_transcript(video["channel"], video["title"], text)
    except Exception as e:
        log.warning("분석 일시 오류 %s: %s", video["video_id"], e)
        return None
    record.update(
        status="analyzed",
        summary=result["summary"],
        opinions=result["opinions"],
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        usage=result.get("usage"),
    )
    log.info(
        "분석 완료: [%s] %s — 의견 %d건",
        video["channel"], video["title"], len(result["opinions"]),
    )
    return record


def git_push():
    """docs/data 변경분을 커밋·푸시한다. 원격이 없으면 커밋만 한다."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)

    run("add", "docs/data")
    diff = run("diff", "--cached", "--quiet")
    if diff.returncode == 0:
        return
    run("commit", "-m", f"data: update {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}Z")
    if "origin" in run("remote").stdout:
        push = run("push")
        if push.returncode != 0:
            log.warning("git push 실패: %s", push.stderr[:300])
        else:
            log.info("GitHub Pages 배포용 push 완료")


def main():
    setup_logging()

    # 하루 1회(오전 7시 이후) 가드 — launchd가 매시간 깨우지만 여기서 거른다.
    # 테스트 등 강제 실행은 --force.
    force = "--force" in sys.argv
    last_run = LAST_RUN_PATH.read_text().strip() if LAST_RUN_PATH.exists() else ""
    if not force and not should_run_daily(datetime.now(), last_run):
        return

    channels = load_json(CHANNELS_PATH, {})["channels"]
    processed = set(load_json(STATE_PATH, []))
    videos_doc = load_json(DATA_DIR / "videos.json", {"videos": []})
    videos = videos_doc["videos"]

    new_videos = collect_new_videos(channels, processed)
    if not new_videos:
        log.info("새 영상 없음")
        _mark_ran_today()
        return

    log.info("새 영상 %d개 처리 시작", len(new_videos))
    app_url = load_env().get("APP_URL")
    notified = []
    for video in new_videos:
        record = process_video(video)
        if record is None:
            continue
        videos.insert(0, record)
        processed.add(video["video_id"])
        if record["status"] == "analyzed":
            notified.append(record)

    videos.sort(key=lambda v: v["published"], reverse=True)
    del videos[MAX_VIDEOS_KEPT:]

    save_json(DATA_DIR / "videos.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "videos": videos,
    })
    save_json(DATA_DIR / "stocks.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stocks": build_stocks(videos),
    })
    save_json(STATE_PATH, sorted(processed))
    update_usage_log(notified)

    git_push()

    for record in notified:
        try:
            send_notification(format_video_message(record, app_url))
        except Exception as e:
            log.warning("텔레그램 전송 실패: %s", e)

    _mark_ran_today()


def update_usage_log(analyzed_records: list[dict], keep_days: int = 30):
    """분석 호출별 토큰 사용량을 docs/data/usage.json에 누적한다 (앱 '사용량' 탭용)."""
    path = DATA_DIR / "usage.json"
    entries = load_json(path, {"entries": []})["entries"]
    for r in analyzed_records:
        if not r.get("usage"):
            continue
        entries.append({
            "ts": r["analyzed_at"],
            "video_id": r["video_id"],
            "channel": r["channel"],
            **r["usage"],
        })
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    entries = [e for e in entries
               if datetime.fromisoformat(e["ts"]).timestamp() >= cutoff]
    save_json(path, {"generated_at": datetime.now(timezone.utc).isoformat(),
                     "entries": entries})


def _mark_ran_today():
    LAST_RUN_PATH.parent.mkdir(exist_ok=True)
    LAST_RUN_PATH.write_text(datetime.now().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    sys.exit(main())
