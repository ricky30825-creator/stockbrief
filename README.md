# 주식 브리핑 (StockBrief)

주식 유튜버 5개 채널의 새 영상을 자동 분석해 **종목별 매수/매도 의견과 근거**를
아이폰 홈화면 웹앱(PWA)으로 보여주는 개인용 도구.

- 채널: 토마토증권 · 삼프로TV · 전인구경제연구소 · 소수몽키 · 미국주식으로은퇴하기
- 분석: 유튜브 자막 → `claude -p` (Claude 구독 내 사용, 추가 비용 없음)
- 호스팅: GitHub Pages (`docs/`) · 알림: 텔레그램 봇

## 구조

```
analyzer/   Mac에서 launchd로 1시간마다 실행되는 파이프라인
  main.py        전체 흐름 (감지→자막→분석→저장→push→알림)
  feeds.py       채널 RSS에서 새 영상 감지
  transcript.py  자막 추출 (60,000자 초과 시 샘플링 절단)
  analyze.py     claude CLI 헤드리스 분석 → 의견 JSON
  aggregate.py   종목 기준 재집계 (stocks.json)
  notify.py      텔레그램 알림
  channels.json  채널 설정 (추가/교체는 이 파일 수정)
  state/         처리한 video_id 기록 (git 제외)
docs/       PWA + 데이터 (GitHub Pages 루트)
  data/videos.json  영상별 분석 (최신 200개)
  data/stocks.json  종목별 의견 집계
launchd/    자동 실행 설정
```

## 운영

실행 주기: launchd가 매시간 깨우지만, 스크립트가 **오전 7시 이후 하루 1회**만
실제로 실행한다 (`analyzer/state/last_run.txt` 기준). 7시 이후에 Mac을 켜면
그때 바로 실행된다.

```bash
# 수동 실행 (하루 1회 가드 무시)
.venv/bin/python analyzer/main.py --force

# 테스트
.venv/bin/python -m unittest discover -s analyzer/tests

# 자동 실행 등록 / 해제 / 즉시 실행
cp launchd/com.jungjeahwan.stockbrief.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jungjeahwan.stockbrief.plist
launchctl bootout gui/$(id -u)/com.jungjeahwan.stockbrief
launchctl kickstart gui/$(id -u)/com.jungjeahwan.stockbrief
```

`analyzer/.env` (git 제외):

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
APP_URL=https://<github계정>.github.io/<repo>/
```

## 아이폰 설치

Safari에서 GitHub Pages URL 접속 → 공유 버튼 → **홈 화면에 추가**.

> 면책: 자동 요약은 투자 판단의 참고 자료일 뿐 투자 권유가 아닙니다.
