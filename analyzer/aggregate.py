"""영상별 분석 결과를 종목 기준으로 재집계해 stocks.json 데이터를 만든다."""


def build_stocks(videos: list[dict]) -> list[dict]:
    """videos.json의 영상 목록 → 종목별 의견 집계 목록 (최근 의견순 정렬)."""
    stocks: dict[str, dict] = {}
    for video in videos:
        for op in video.get("opinions") or []:
            # 미국 주식은 심볼이 신뢰할 만하지만, 한국 종목 코드는 LLM이
            # 잘못 추측할 수 있어 종목명(공백 제거)으로 묶는다.
            if op.get("market") == "US" and op.get("ticker"):
                key = op["ticker"].upper()
            else:
                key = op["stock"].replace(" ", "").upper()
            entry = stocks.setdefault(
                key,
                {
                    "stock": op["stock"],
                    "ticker": op.get("ticker"),
                    "market": op.get("market"),
                    "buy": 0,
                    "sell": 0,
                    "hold": 0,
                    "opinions": [],
                },
            )
            if op["stance"] == "매수":
                entry["buy"] += 1
            elif op["stance"] == "매도":
                entry["sell"] += 1
            else:
                entry["hold"] += 1
            entry["opinions"].append(
                {
                    "channel": video["channel"],
                    "stance": op["stance"],
                    "reasoning": op["reasoning"],
                    "confidence": op["confidence"],
                    "video_id": video["video_id"],
                    "title": video["title"],
                    "published": video["published"],
                    "url": video["url"],
                }
            )
    result = list(stocks.values())
    for entry in result:
        entry["opinions"].sort(key=lambda o: o["published"], reverse=True)
    result.sort(key=lambda s: s["opinions"][0]["published"], reverse=True)
    return result
