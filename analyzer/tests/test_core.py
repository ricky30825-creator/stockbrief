import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from datetime import datetime, timezone

from aggregate import build_stocks
from main import transcript_wait_expired
from analyze import _parse_result, extract_json
from feeds import parse_feed
from notify import format_video_message
from transcript import truncate_evenly

SAMPLE_FEED = """<?xml version="1.0"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>채널명</title>
 <entry>
  <yt:videoId>abc123</yt:videoId>
  <title>영상 제목</title>
  <published>2026-06-10T09:00:00+00:00</published>
 </entry>
</feed>"""


class TestFeeds(unittest.TestCase):
    def test_parse_feed(self):
        videos = parse_feed(SAMPLE_FEED)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["video_id"], "abc123")
        self.assertEqual(videos[0]["title"], "영상 제목")
        self.assertIn("watch?v=abc123", videos[0]["url"])


class TestTranscript(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(truncate_evenly("짧은 자막", 100), "짧은 자막")

    def test_long_text_sampled(self):
        text = "A" * 50000 + "B" * 50000 + "C" * 50000
        out = truncate_evenly(text, 60000)
        self.assertLess(len(out), 61000)
        self.assertIn("A", out)
        self.assertIn("B", out)
        self.assertIn("C", out)


class TestAnalyze(unittest.TestCase):
    def test_extract_json_with_fence(self):
        raw = '결과입니다:\n```json\n{"summary": "요약", "opinions": []}\n```'
        self.assertEqual(extract_json(raw)["summary"], "요약")

    def test_extract_json_trailing_text(self):
        raw = '{"summary": "요약", "opinions": []} 이상입니다.'
        self.assertEqual(extract_json(raw)["opinions"], [])

    def test_parse_result_filters_bad_opinions(self):
        raw = """{"summary": "요약", "opinions": [
          {"stock": "삼성전자", "ticker": "005930", "market": "KR",
           "stance": "매수", "reasoning": "근거", "confidence": "high"},
          {"stock": "", "stance": "매수"},
          {"stock": "테슬라", "stance": "강력매수"}
        ]}"""
        result = _parse_result(raw)
        self.assertEqual(len(result["opinions"]), 1)
        self.assertEqual(result["opinions"][0]["stock"], "삼성전자")

    def test_parse_result_defaults(self):
        raw = '{"summary": "s", "opinions": [{"stock": "엔비디아", "stance": "매수", "market": "USA", "confidence": "최상"}]}'
        op = _parse_result(raw)["opinions"][0]
        self.assertIsNone(op["market"])
        self.assertEqual(op["confidence"], "medium")
        self.assertIsNone(op["ticker"])


def _video(video_id, channel, published, opinions):
    return {
        "video_id": video_id, "channel": channel, "title": "t",
        "published": published, "url": "u", "opinions": opinions,
    }


class TestAggregate(unittest.TestCase):
    def test_groups_by_ticker_and_counts(self):
        videos = [
            _video("v1", "채널A", "2026-06-10T00:00:00+00:00", [
                {"stock": "삼성전자", "ticker": "005930", "market": "KR",
                 "stance": "매수", "reasoning": "r1", "confidence": "high"},
            ]),
            _video("v2", "채널B", "2026-06-11T00:00:00+00:00", [
                {"stock": "삼성전자", "ticker": "005930", "market": "KR",
                 "stance": "매도", "reasoning": "r2", "confidence": "medium"},
            ]),
        ]
        stocks = build_stocks(videos)
        self.assertEqual(len(stocks), 1)
        s = stocks[0]
        self.assertEqual((s["buy"], s["sell"], s["hold"]), (1, 1, 0))
        self.assertEqual(s["opinions"][0]["channel"], "채널B")  # 최신순

    def test_us_stocks_grouped_by_ticker_despite_name_variants(self):
        videos = [
            _video("v1", "A", "2026-06-10T00:00:00+00:00", [
                {"stock": "엔비디아", "ticker": "nvda", "market": "US",
                 "stance": "매수", "reasoning": "r", "confidence": "high"},
            ]),
            _video("v2", "B", "2026-06-11T00:00:00+00:00", [
                {"stock": "NVIDIA", "ticker": "NVDA", "market": "US",
                 "stance": "매도", "reasoning": "r", "confidence": "high"},
            ]),
        ]
        stocks = build_stocks(videos)
        self.assertEqual(len(stocks), 1)

    def test_kr_stocks_grouped_by_name_not_unreliable_ticker(self):
        videos = [
            _video("v1", "A", "2026-06-10T00:00:00+00:00", [
                {"stock": "삼성전자", "ticker": "006400", "market": "KR",
                 "stance": "매수", "reasoning": "r", "confidence": "high"},
            ]),
            _video("v2", "B", "2026-06-11T00:00:00+00:00", [
                {"stock": "삼성 전자", "ticker": "005930", "market": "KR",
                 "stance": "보유", "reasoning": "r", "confidence": "high"},
            ]),
        ]
        stocks = build_stocks(videos)
        self.assertEqual(len(stocks), 1)

    def test_sorted_by_latest_opinion(self):
        videos = [
            _video("v1", "A", "2026-06-11T00:00:00+00:00", [
                {"stock": "테슬라", "ticker": "TSLA", "market": "US",
                 "stance": "매수", "reasoning": "r", "confidence": "high"},
            ]),
            _video("v2", "B", "2026-06-09T00:00:00+00:00", [
                {"stock": "애플", "ticker": "AAPL", "market": "US",
                 "stance": "관망", "reasoning": "r", "confidence": "low"},
            ]),
        ]
        stocks = build_stocks(videos)
        self.assertEqual([s["stock"] for s in stocks], ["테슬라", "애플"])


class TestTranscriptWait(unittest.TestCase):
    NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)

    def test_fresh_video_waits(self):
        self.assertFalse(transcript_wait_expired("2026-06-11T10:00:00+00:00", self.NOW))

    def test_old_video_expired(self):
        self.assertTrue(transcript_wait_expired("2026-06-10T12:00:00+00:00", self.NOW))

    def test_bad_date_expired(self):
        self.assertTrue(transcript_wait_expired("invalid", self.NOW))


class TestNotify(unittest.TestCase):
    def test_message_format(self):
        video = _video("v1", "소수몽키", "2026-06-11T00:00:00+00:00", [
            {"stock": "엔비디아", "stance": "매수", "reasoning": "", "confidence": "high"},
            {"stock": "테슬라", "stance": "매도", "reasoning": "", "confidence": "low"},
        ])
        video["title"] = "AI 랠리 점검"
        msg = format_video_message(video)
        self.assertIn("🟢 매수: 엔비디아", msg)
        self.assertIn("🔴 매도: 테슬라", msg)

    def test_no_opinions(self):
        video = _video("v1", "삼프로TV", "2026-06-11T00:00:00+00:00", [])
        video["title"] = "시황"
        self.assertIn("종목 의견 없음", format_video_message(video))


if __name__ == "__main__":
    unittest.main()
