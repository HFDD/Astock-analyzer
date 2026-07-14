"""
Deterministic research-note builders for A-share workflows.

The morning note is a read-only derived artifact. It does not persist data,
call external LLMs, or produce trading instructions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


STRATEGY_LABELS = {
    "first_board_relay": "低位首板接力观察",
    "leader_chase": "龙头延续观察",
    "etf_rotation": "ETF轮动观察",
    "qlib_model": "Qlib模型研究参考",
}


def build_morning_note(daily_picks: dict | None, heat_ranking: dict | None, date: str | None = None) -> dict:
    """Build a concise deterministic morning note from existing project data."""
    daily = daily_picks if isinstance(daily_picks, dict) else {}
    heat = heat_ranking if isinstance(heat_ranking, dict) else {}
    trade_date = _resolve_trade_date(daily, date)
    hot_topics = _normalize_hot_topics(heat.get("items"))
    strategy_ideas = _normalize_strategy_ideas(daily.get("groups"))
    data_status = _build_data_status(daily, heat, hot_topics)
    market_context = _build_market_context(heat, hot_topics, data_status)
    top_call = _build_top_call(hot_topics, strategy_ideas, data_status)
    risk_notes = _build_risk_notes(data_status, hot_topics)

    note = {
        "trade_date": trade_date,
        "latest_trade_date": daily.get("latest_trade_date") or trade_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "top_call": top_call,
        "market_context": market_context,
        "hot_topics": hot_topics,
        "strategy_ideas": strategy_ideas,
        "risk_notes": risk_notes,
        "data_status": data_status,
    }
    note["markdown"] = generate_morning_note_markdown(note)
    return note


def generate_morning_note_markdown(note: dict) -> str:
    """Render a morning-note dict as Markdown for copying or display."""
    trade_date = note.get("trade_date") or "--"
    lines = [
        f"# {trade_date} A股晨会稿",
        "",
        "## Top Call",
        f"- {note.get('top_call') or '暂无高置信热榜，先以风控观察为主。'}",
        "",
        "## 资金进入与散户流量榜",
    ]

    hot_topics = note.get("hot_topics") if isinstance(note.get("hot_topics"), list) else []
    if hot_topics:
        for topic in hot_topics:
            lines.extend(_topic_markdown_lines(topic))
    else:
        lines.append("- 暂无高置信热榜：资金进入与外部流量数据不足，先观察数据源恢复和盘中确认。")

    lines.extend(["", "## 今日策略观察"])
    strategy_ideas = note.get("strategy_ideas") if isinstance(note.get("strategy_ideas"), list) else []
    if strategy_ideas:
        for idea in strategy_ideas:
            lines.extend(_strategy_markdown_lines(idea))
    else:
        lines.append("- 暂无策略候选：今日先以盘面确认和自选股复盘为主。")

    lines.extend(["", "## 风控提示"])
    for risk in note.get("risk_notes") or []:
        lines.append(f"- {risk}")

    return "\n".join(lines).strip() + "\n"


def _resolve_trade_date(daily: dict, date: str | None) -> str:
    if date:
        return str(date)
    for key in ("trade_date", "latest_trade_date"):
        value = daily.get(key)
        if value:
            return str(value)
    return datetime.now().strftime("%Y-%m-%d")


def _normalize_hot_topics(items: Any) -> list[dict]:
    result = []
    for index, raw in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("topic_name") or raw.get("sector_name"))
        if not name:
            continue
        related_etfs = _normalize_related_etfs(raw.get("related_etfs"))
        leaders = _normalize_leaders(raw.get("leaders"))
        topic = {
            "rank": raw.get("rank") or index,
            "topic_name": name,
            "topic_type": _clean_text(raw.get("topic_type") or raw.get("sector_type")) or "topic",
            "entry_power_score": _round(raw.get("entry_power_score") or raw.get("sector_heat_score") or raw.get("heat")),
            "capital_inflow_amount": raw.get("capital_inflow_amount"),
            "capital_source": _clean_text(raw.get("capital_source") or raw.get("source")),
            "capital_window": _clean_text(raw.get("capital_window")),
            "confidence": _round(raw.get("confidence"), 2),
            "risk_flags": _text_list(raw.get("risk_flags")),
            "warnings": _text_list(raw.get("warnings")),
            "leaders": leaders,
            "related_etfs": related_etfs,
        }
        topic["summary"] = _topic_summary(topic)
        result.append(topic)
    return result[:5]


def _normalize_related_etfs(etfs: Any) -> list[dict]:
    result = []
    for raw in etfs if isinstance(etfs, list) else []:
        if not isinstance(raw, dict):
            continue
        code = _clean_text(raw.get("code") or raw.get("stock_code"))
        name = _clean_text(raw.get("name") or raw.get("stock_name"))
        if not code and not name:
            continue
        source = _clean_text(raw.get("source"))
        result.append(
            {
                "code": code,
                "name": name,
                "source": source or "keyword_map",
                "source_label": "行情补充" if source == "keyword_map+tencent_quote" else "关键词映射",
                "relevance_score": _round(raw.get("relevance_score")),
                "etf_entry_score": _round(raw.get("etf_entry_score")),
                "change_pct": _round(raw.get("change_pct"), 2),
            }
        )
    return result[:2]


def _normalize_leaders(leaders: Any) -> list[dict]:
    result = []
    for raw in leaders if isinstance(leaders, list) else []:
        if not isinstance(raw, dict):
            continue
        code = _clean_text(raw.get("code") or raw.get("stock_code"))
        name = _clean_text(raw.get("name") or raw.get("stock_name"))
        if not code and not name:
            continue
        result.append(
            {
                "code": code,
                "name": name,
                "leader_label": _clean_text(raw.get("leader_label")) or "龙头候选",
                "leader_entry_score": _round(raw.get("leader_entry_score")),
            }
        )
    return result[:3]


def _normalize_strategy_ideas(groups: Any) -> list[dict]:
    result = []
    for raw in groups if isinstance(groups, list) else []:
        if not isinstance(raw, dict):
            continue
        key = _clean_text(raw.get("strategy_key"))
        recommendations = raw.get("recommendations") if isinstance(raw.get("recommendations"), list) else []
        picks = []
        for item in recommendations[:2]:
            if not isinstance(item, dict):
                continue
            code = _clean_text(item.get("code") or item.get("stock_code"))
            name = _clean_text(item.get("name") or item.get("stock_name"))
            picks.append(
                {
                    "code": code,
                    "name": name,
                    "score": _round(item.get("score")),
                    "mode": _clean_text(item.get("mode")),
                    "reasons": _text_list(item.get("reasons"))[:2],
                    "risks": _text_list(item.get("risks"))[:2],
                }
            )
        idea = {
            "strategy_key": key,
            "strategy_name": _clean_text(raw.get("strategy_name")) or STRATEGY_LABELS.get(key, "策略观察"),
            "status": _clean_text(raw.get("status")) or "empty",
            "total_scanned": raw.get("total_scanned"),
            "total_picks": raw.get("total_picks") if raw.get("total_picks") is not None else len(recommendations),
            "picks": picks,
            "summary": _strategy_summary(key, raw, picks),
        }
        if key == "qlib_model":
            idea["research_note"] = "Qlib模型组仅作量化研究与观察参考，不构成自动买入、卖出或调仓建议。"
        result.append(idea)
    return result


def _build_data_status(daily: dict, heat: dict, hot_topics: list[dict]) -> dict:
    heat_status = heat.get("data_status") if isinstance(heat.get("data_status"), dict) else {}
    daily_status = daily.get("data_status") if isinstance(daily.get("data_status"), dict) else {}
    warnings = []
    warnings.extend(_text_list(daily_status.get("warnings")))
    warnings.extend(_text_list(heat_status.get("warnings")))
    for topic in hot_topics:
        warnings.extend(topic.get("warnings") or [])
    status = heat_status.get("status") or daily_status.get("status") or "ok"
    if warnings or status in {"degraded", "fallback", "failed", "unavailable"}:
        status = "degraded"
    return {
        "status": status,
        "source": heat.get("source") or "unavailable",
        "window": heat.get("window") or "latest",
        "confidence": _round(heat.get("confidence"), 2),
        "warnings": _unique_text(warnings),
    }


def _build_market_context(heat: dict, hot_topics: list[dict], data_status: dict) -> dict:
    if hot_topics:
        names = "、".join(topic["topic_name"] for topic in hot_topics[:3])
        summary = f"热榜前列为 {names}，用于观察资金进入与外部流量共振。"
    else:
        summary = "暂无高置信热榜，先把热度数据视为降级观察。"
    return {
        "summary": summary,
        "source": data_status.get("source"),
        "window": data_status.get("window"),
        "confidence": data_status.get("confidence"),
        "status": data_status.get("status"),
        "item_count": len(hot_topics),
    }


def _build_top_call(hot_topics: list[dict], strategy_ideas: list[dict], data_status: dict) -> str:
    pick_count = sum(len(idea.get("picks") or []) for idea in strategy_ideas)
    if not hot_topics:
        return "暂无高置信热榜，今日以策略候选复盘、数据源恢复和盘中风控观察为主。"
    top = hot_topics[0]
    score = _format_score(top.get("entry_power_score"))
    suffix = f"，并结合 {pick_count} 个策略候选做研究参考" if pick_count else ""
    if data_status.get("status") == "degraded":
        suffix += "；当前存在数据降级，需盘中确认"
    return f"今日观察重点：{top['topic_name']} 入场能量 {score} 居前{suffix}。"


def _build_risk_notes(data_status: dict, hot_topics: list[dict]) -> list[str]:
    risks = []
    warnings = data_status.get("warnings") or []
    if warnings:
        risks.append("数据源提示：" + "；".join(warnings[:3]))
    if data_status.get("status") == "degraded":
        risks.append("热榜或策略数据存在降级，结论只能作为盘前观察线索。")
    if any(topic.get("related_etfs") for topic in hot_topics):
        risks.append("相关 ETF 来自题材关键词映射或行情补充，只表达板块观察关系，不代表 ETF 资金流排名。")
    risks.append("所有内容仅供研究参考，不构成投资建议。")
    return _unique_text(risks)


def _topic_summary(topic: dict) -> str:
    score = _format_score(topic.get("entry_power_score"))
    parts = [f"{topic['topic_name']} 入场能量 {score}"]
    if topic.get("leaders"):
        leader = topic["leaders"][0]
        parts.append(f"龙头候选 {leader.get('code') or '--'} {leader.get('name') or ''}".strip())
    if topic.get("related_etfs"):
        parts.append("可观察相关 ETF")
    return "，".join(parts)


def _strategy_summary(key: str, group: dict, picks: list[dict]) -> str:
    name = _clean_text(group.get("strategy_name")) or STRATEGY_LABELS.get(key, "策略观察")
    status = _clean_text(group.get("status")) or "empty"
    if picks:
        first = picks[0]
        label = f"{first.get('code') or '--'} {first.get('name') or ''}".strip()
        return f"{name}：{status}，首个观察标的 {label}。"
    return f"{name}：{status}，暂无候选，保持观察。"


def _topic_markdown_lines(topic: dict) -> list[str]:
    lines = [f"- #{topic.get('rank')} {topic.get('topic_name')}：{topic.get('summary')}。"]
    if topic.get("risk_flags"):
        lines.append(f"  - 风险标记：{'、'.join(topic['risk_flags'][:3])}")
    if topic.get("leaders"):
        leaders = [
            f"{leader.get('code') or '--'} {leader.get('name') or ''}（{leader.get('leader_label') or '龙头候选'}）".strip()
            for leader in topic["leaders"][:3]
        ]
        lines.append(f"  - 龙头候选：{'；'.join(leaders)}")
    if topic.get("related_etfs"):
        etfs = [
            f"{etf.get('code') or '--'} {etf.get('name') or ''}（{etf.get('source_label') or '关键词映射'}）".strip()
            for etf in topic["related_etfs"][:2]
        ]
        lines.append(f"  - 相关 ETF：{'；'.join(etfs)}")
    return lines


def _strategy_markdown_lines(idea: dict) -> list[str]:
    lines = [f"- {idea.get('strategy_name')}：{idea.get('summary')}"]
    for pick in idea.get("picks") or []:
        label = f"{pick.get('code') or '--'} {pick.get('name') or ''}".strip()
        reasons = "；".join(pick.get("reasons") or [])
        risks = "；".join(pick.get("risks") or [])
        detail = f"  - {label}"
        if pick.get("mode"):
            detail += f" · {pick['mode']}"
        if pick.get("score") is not None:
            detail += f" · 评分 {_format_score(pick.get('score'))}"
        if reasons:
            detail += f" · 观察理由：{reasons}"
        if risks:
            detail += f" · 风险：{risks}"
        lines.append(detail)
    if idea.get("research_note"):
        lines.append(f"  - {idea['research_note']}")
    return lines


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _unique_text(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _round(value: Any, digits: int = 1) -> float | None:
    try:
        if value is None or value == "":
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _format_score(value: Any) -> str:
    numeric = _round(value)
    if numeric is None:
        return "--"
    return f"{numeric:g}"
