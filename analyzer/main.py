"""전체 파이프라인: 새 영상 감지 → 텔레그램 y/n 승인 → 자막 → 분석 → 데이터 갱신 → push → 알림.

매일 아침 7시 이후 새 영상이 있으면 "분석할까요? (y/n)"를 텔레그램으로 묻고,
y 답장이 와야 분석한다. n이면 그날은 건너뛰고, 무응답이면 다음 날 다시 묻는다.
launchd가 매시간 깨우면 상태(state/pending.json)에 따라 질문/답확인/분석으로 분기한다.
여러 번 실행해도 안전하다(멱등): 처리한 video_id는 state/processed.json에 기록된다.
"""

import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from aggregate import build_stocks
from analyze import analyze_transcript
from feeds import fetch_channel_videos
from notify import fetch_replies, format_video_message, load_env, send_notification
from transcript import TransientFetchError, get_transcript

BASE = Path(__file__).parent
REPO = BASE.parent
DATA_DIR = REPO / "docs" / "data"
STATE_PATH = BASE / "state" / "processed.json"
LAST_RUN_PATH = BASE / "state" / "last_run.txt"
PENDING_PATH = BASE / "state" / "pending.json"
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


def collect_new_videos(channels: list[dict], processed: set[str]) -> list[dict] | None:
    """모든 채널에서 미처리 영상을 모은다 (최신 우선, 상한 적용).

    전 채널 조회가 실패하면(부팅 직후 네트워크 미연결 등) None을 반환한다 —
    이때는 '새 영상 없음'과 달리 완료 처리하지 말고 다음 주기에 재시도해야 한다.
    """
    first_run = not processed
    new_videos, failures = [], 0
    for ch in channels:
        try:
            feed = fetch_channel_videos(ch["channel_id"])
        except Exception as e:
            log.warning("피드 조회 실패 %s: %s", ch["name"], e)
            failures += 1
            continue
        fresh = [v for v in feed if v["video_id"] not in processed]
        if first_run:
            fresh = fresh[:FIRST_RUN_PER_CHANNEL]
        for v in fresh:
            v["channel"] = ch["name"]
            v["channel_id"] = ch["channel_id"]
        new_videos.extend(fresh)
    if channels and failures == len(channels):
        return None
    new_videos.sort(key=lambda v: v["published"], reverse=True)
    return new_videos[:MAX_PER_RUN]  # 최신 우선, 나머지 백필은 다음 주기로


def parse_yn(texts: list[str]) -> str | None:
    """답장 목록에서 y/n을 찾는다 (대소문자·공백 무시, 마지막 답 채택)."""
    answer = None
    for t in texts:
        s = (t or "").strip().lower()
        if s in ("y", "n"):
            answer = s
    return answer


def build_question(new_videos: list[dict]) -> str:
    """새 영상 요약과 함께 분석 여부를 묻는 메시지 (반드시 (y/n)으로 끝남)."""
    by_channel: dict[str, list[dict]] = {}
    for v in new_videos:
        by_channel.setdefault(v["channel"], []).append(v)
    lines = [f"📋 새 영상 {len(new_videos)}개 발견"]
    for ch, items in by_channel.items():
        title = items[0]["title"]
        suffix = "…" if len(title) > 30 else ""
        lines.append(f"• {ch} {len(items)}개 — {title[:30]}{suffix}")
    lines.append("")
    lines.append("분석할까요? (y/n)")
    return "\n".join(lines)


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


def run_analysis(channels: list[dict], processed: set[str]):
    """승인된(또는 --force) 분석 본체: 수집→분석→저장→push→영상별 알림."""
    videos_doc = load_json(DATA_DIR / "videos.json", {"videos": []})
    videos = videos_doc["videos"]

    new_videos = collect_new_videos(channels, processed)
    if new_videos is None:
        raise RuntimeError("전 채널 피드 조회 실패 (네트워크 장애?) — 다음 주기에 재시도")
    if not new_videos:
        log.info("새 영상 없음")
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


def main():
    setup_logging()
    channels = load_json(CHANNELS_PATH, {})["channels"]
    processed = set(load_json(STATE_PATH, []))

    if "--force" in sys.argv:
        run_analysis(channels, processed)
        _finish_today()
        return

    now = datetime.now()
    last_run = LAST_RUN_PATH.read_text().strip() if LAST_RUN_PATH.exists() else ""
    if not should_run_daily(now, last_run):
        return

    today = now.strftime("%Y-%m-%d")
    pending = load_json(PENDING_PATH, {})

    if pending.get("date") != today:
        # 오늘 아직 질문 전 → 새 영상 확인 후 분석 여부 질문
        new_videos = collect_new_videos(channels, processed)
        if new_videos is None:
            log.info("전 채널 피드 조회 실패 (네트워크 미연결?) — 다음 주기에 재시도")
            return
        if not new_videos:
            log.info("새 영상 없음")
            _finish_today()
            return
        if send_notification(build_question(new_videos)):
            save_json(PENDING_PATH, {"date": today, "asked_at": time.time(), "answered": None})
            log.info("분석 여부 질문 전송 (영상 %d개) — y/n 답 대기", len(new_videos))
        else:
            # 텔레그램 미설정이면 묻지 않고 바로 분석 (이전 동작 유지)
            run_analysis(channels, processed)
            _finish_today()
        return

    # 오늘 질문을 보냈음 → 답 확인
    if pending.get("answered") != "y":
        answer = parse_yn(fetch_replies(pending.get("asked_at", 0)))
        if answer == "n":
            send_notification("알겠습니다. 오늘 분석은 건너뛸게요. 내일 새 영상으로 다시 물어보겠습니다.")
            log.info("사용자 답: n — 오늘 분석 건너뜀")
            _finish_today()
            return
        if answer != "y":
            log.info("답 대기 중 — 다음 주기에 다시 확인")
            return
        # 분석 도중 실패해도 y 답을 잃지 않도록 먼저 기록 (다음 주기에 분석만 재시도)
        pending["answered"] = "y"
        save_json(PENDING_PATH, pending)
        log.info("사용자 답: y — 분석 시작")

    run_analysis(channels, processed)
    _finish_today()


def _finish_today():
    LAST_RUN_PATH.parent.mkdir(exist_ok=True)
    LAST_RUN_PATH.write_text(datetime.now().strftime("%Y-%m-%d"))
    PENDING_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
