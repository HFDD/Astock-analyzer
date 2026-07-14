"""
Optional OpenAI-compatible enhancement for user quant strategy analysis.

This module is intentionally opt-in. Without all required environment
variables, no user strategy source is sent to any external service.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional


def llm_enhancement_enabled() -> bool:
    return all(
        os.getenv(key)
        for key in (
            "STOCK_ANALYZER_LLM_API_KEY",
            "STOCK_ANALYZER_LLM_BASE_URL",
            "STOCK_ANALYZER_LLM_MODEL",
        )
    )


def enhance_quant_strategy_analysis(source_code: str, parse_result: dict, platform: str) -> Optional[dict]:
    """Enhance static analysis through an OpenAI-compatible chat endpoint."""
    if not llm_enhancement_enabled():
        return None

    base_url = os.environ["STOCK_ANALYZER_LLM_BASE_URL"].rstrip("/")
    api_key = os.environ["STOCK_ANALYZER_LLM_API_KEY"]
    model = os.environ["STOCK_ANALYZER_LLM_MODEL"]
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股量化策略审阅助手。只解释用户策略中的选股逻辑、买点逻辑、卖点/风控逻辑和风险。"
                    "不要承诺收益，不要生成自动下单建议。必须说明仅供量化研究参考，不构成投资建议。"
                    "返回 JSON，字段为 summary, selection_logic, buy_logic, sell_logic, risk_notes。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "platform": platform,
                        "static_parse": parse_result,
                        "source_code": source_code,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason or exc)) from exc

    content = (
        body.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if not content:
        raise RuntimeError("LLM 返回内容为空")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"summary": content}
    parsed.setdefault("disclaimer", "仅供量化研究参考，不构成投资建议。")
    return parsed
