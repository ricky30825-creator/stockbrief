"""자막을 claude CLI(헤드리스)로 분석해 종목별 매수/매도 의견을 추출한다.

API 키 대신 로그인된 Claude 구독을 사용하므로 추가 비용이 없다.
"""

import json
import re
import subprocess

CLAUDE_MODEL = "haiku"
TIMEOUT_SEC = 300

PROMPT = """당신은 주식 유튜브 영상 자막을 분석하는 애널리스트다.
아래는 유튜브 채널 "{channel}"의 영상 "{title}" 자막 전문이다.

자막에서 발화자가 개별 종목(또는 ETF)에 대해 밝힌 투자 의견을 추출하라.

규칙:
- 발화자가 실제로 언급한 종목만 추출한다. 추측으로 만들어내지 않는다.
- stance는 발화 취지에 따라 "매수" / "매도" / "보유" / "관망" 중 하나.
- reasoning은 발화자가 영상에서 직접 제시한 근거를 2~3문장으로 한국어 요약.
- confidence: 명시적 추천이면 "high", 긍정적/부정적 뉘앙스 수준이면 "medium", 짧은 스치는 언급이면 "low".
- ticker: 한국 종목은 6자리 코드(모르면 null), 미국 종목은 심볼(예: AAPL). market은 "KR" 또는 "US".
- 시황·거시경제만 다루고 종목 의견이 없으면 opinions를 빈 배열로 둔다.
- summary는 영상 전체 내용의 3~4문장 한국어 요약.

다른 텍스트 없이 아래 형식의 JSON만 출력하라:
{{"summary": "...", "opinions": [{{"stock": "...", "ticker": "... 또는 null", "market": "KR|US", "stance": "매수|매도|보유|관망", "reasoning": "...", "confidence": "high|medium|low"}}]}}

=== 자막 시작 ===
{transcript}
=== 자막 끝 ==="""


def analyze_transcript(channel: str, title: str, transcript: str) -> dict:
    """자막을 분석해 {"summary": str, "opinions": [...]} 반환. 실패 시 1회 재시도."""
    prompt = PROMPT.format(channel=channel, title=title, transcript=transcript)
    last_err = None
    for _ in range(2):
        try:
            raw = _run_claude(prompt)
            return _parse_result(raw)
        except (ValueError, subprocess.SubprocessError) as e:
            last_err = e
    raise RuntimeError(f"분석 실패 ({channel} / {title}): {last_err}")


def _run_claude(prompt: str) -> str:
    proc = subprocess.run(
        ["claude", "-p", "--model", CLAUDE_MODEL],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise subprocess.SubprocessError(f"claude CLI 오류: {proc.stderr[:500]}")
    return proc.stdout


def _parse_result(raw: str) -> dict:
    data = extract_json(raw)
    if not isinstance(data, dict) or "summary" not in data:
        raise ValueError(f"응답에 summary 없음: {raw[:200]}")
    opinions = []
    for op in data.get("opinions") or []:
        if not op.get("stock") or op.get("stance") not in ("매수", "매도", "보유", "관망"):
            continue
        opinions.append(
            {
                "stock": str(op["stock"]).strip(),
                "ticker": op.get("ticker") or None,
                "market": op.get("market") if op.get("market") in ("KR", "US") else None,
                "stance": op["stance"],
                "reasoning": str(op.get("reasoning") or "").strip(),
                "confidence": op.get("confidence")
                if op.get("confidence") in ("high", "medium", "low")
                else "medium",
            }
        )
    return {"summary": str(data["summary"]).strip(), "opinions": opinions}


def extract_json(text: str):
    """LLM 출력에서 첫 JSON 객체를 찾아 파싱한다 (코드펜스 등 잡음 허용)."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"JSON 없음: {text[:200]}")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    return obj
