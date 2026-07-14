"""
Static parser for user-pasted quant strategy source.

The parser deliberately never executes user code. It only inspects Python AST
and source text so pasted JoinQuant-style strategies can be stored and reviewed
without creating a runtime execution surface.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Optional


DEFAULT_PLATFORM = "python_joinquant"
RESEARCH_ONLY_WARNING = "解析结果仅供量化研究参考，不构成投资建议或自动交易信号。"

SELECTION_TERMS = (
    "get_index_stocks",
    "get_all_securities",
    "get_fundamentals",
    "query",
    "filter",
    "valuation",
    "market_cap",
    "circulating_market_cap",
    "stock_pool",
    "candidates",
    "candidate",
    "股票池",
    "选股",
)
BUY_TERMS = (
    "order_value",
    "order",
    "buy",
    "开仓",
    "买入",
    "突破",
    "放量",
    "竞价",
    "连板",
    "momentum",
)
SELL_TERMS = (
    "order_target",
    "order_target_value",
    "sell",
    "清仓",
    "卖出",
    "止损",
    "止盈",
    "破位",
    "减仓",
)
RISK_TERMS = (
    "stop_loss",
    "stop_profit",
    "take_profit",
    "risk",
    "max_drawdown",
    "position",
    "仓位",
    "风控",
    "止损",
    "止盈",
)
INDICATOR_TERMS = {
    "ma": ("ma", "均线", "mean", "rolling"),
    "macd": ("macd",),
    "kdj": ("kdj",),
    "rsi": ("rsi",),
    "boll": ("boll", "bollinger"),
    "volume": ("volume", "vol", "成交量", "量能"),
    "price": ("price", "close", "open", "high", "low"),
    "market_cap": ("market_cap", "市值"),
    "limit_up": ("high_limit", "涨停", "limit_up"),
}
DATA_DEPENDENCY_TERMS = (
    "get_price",
    "attribute_history",
    "history",
    "get_fundamentals",
    "get_index_stocks",
    "get_all_securities",
    "get_current_data",
    "valuation",
    "indicator",
    "data[",
    "context.portfolio",
)


def analyze_quant_strategy_source(source_code: str, platform: str = DEFAULT_PLATFORM) -> dict:
    """Return a stable parse envelope for a user quant strategy."""
    source_code = source_code or ""
    platform = platform or DEFAULT_PLATFORM
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        parse_error = f"语法错误: 第{exc.lineno or '?'}行 {exc.msg}"
        return {
            "parse_status": "failed",
            "parse_error": parse_error,
            "parse_result": _empty_parse_result(platform, warnings=[parse_error, RESEARCH_ONLY_WARNING]),
        }

    visitor = _StrategyVisitor(source_code)
    visitor.visit(tree)
    lines = [line.rstrip() for line in source_code.splitlines()]
    non_empty_lines = [line for line in lines if line.strip()]

    selection_logic = _extract_logic(lines, SELECTION_TERMS, visitor.conditions)
    buy_logic = _extract_logic(lines, BUY_TERMS, visitor.conditions)
    sell_logic = _extract_logic(lines, SELL_TERMS, visitor.conditions)
    risk_controls = _extract_logic(lines, RISK_TERMS, visitor.conditions)
    indicators = _extract_indicators(source_code)
    data_dependencies = _extract_unique_terms(source_code, DATA_DEPENDENCY_TERMS)

    unresolved_items = []
    if not selection_logic:
        unresolved_items.append("未识别到明确股票池或选股过滤条件")
    if not buy_logic:
        unresolved_items.append("未识别到明确买入/开仓条件")
    if not sell_logic:
        unresolved_items.append("未识别到明确卖出/清仓条件")
    if not risk_controls:
        unresolved_items.append("未识别到明确止损、止盈或仓位风控")

    warnings = []
    if "run_daily_picker" in source_code or "order_" in source_code:
        warnings.append("检测到交易/下单相关函数，本系统仅做静态解析，不执行用户代码。")
    warnings.append(RESEARCH_ONLY_WARNING)

    confidence = _confidence_score(
        selection_logic,
        buy_logic,
        sell_logic,
        risk_controls,
        indicators,
        data_dependencies,
    )
    result = {
        "summary": {
            "platform": platform,
            "title": _guess_title(visitor.functions),
            "functions": visitor.functions,
            "function_count": len(visitor.functions),
            "line_count": len(non_empty_lines),
            "call_count": len(visitor.calls),
        },
        "selection_logic": selection_logic,
        "buy_logic": buy_logic,
        "sell_logic": sell_logic,
        "risk_controls": risk_controls,
        "indicators": indicators,
        "data_dependencies": data_dependencies,
        "unresolved_items": unresolved_items,
        "warnings": warnings,
        "confidence": confidence,
    }
    return {"parse_status": "success", "parse_error": None, "parse_result": result}


def _empty_parse_result(platform: str, warnings: Optional[list[str]] = None) -> dict:
    return {
        "summary": {
            "platform": platform,
            "title": "未能解析策略",
            "functions": [],
            "function_count": 0,
            "line_count": 0,
            "call_count": 0,
        },
        "selection_logic": [],
        "buy_logic": [],
        "sell_logic": [],
        "risk_controls": [],
        "indicators": [],
        "data_dependencies": [],
        "unresolved_items": ["代码语法错误或格式不完整，无法继续静态解析"],
        "warnings": warnings or [RESEARCH_ONLY_WARNING],
        "confidence": 0.0,
    }


class _StrategyVisitor(ast.NodeVisitor):
    def __init__(self, source_code: str):
        self.source_code = source_code
        self.functions: list[str] = []
        self.calls: list[str] = []
        self.conditions: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.functions.append(node.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        name = _call_name(node.func)
        if name:
            self.calls.append(name)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> Any:
        segment = ast.get_source_segment(self.source_code, node.test)
        if segment:
            self.conditions.append(_compact(segment))
        self.generic_visit(node)


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _extract_logic(lines: list[str], terms: tuple[str, ...], conditions: list[str]) -> list[str]:
    matches: list[str] = []
    term_pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    for line in lines:
        stripped = line.strip()
        if stripped and term_pattern.search(stripped):
            matches.append(stripped)
    for condition in conditions:
        if term_pattern.search(condition):
            matches.append(f"条件: {condition}")
    return _dedupe(matches, limit=8)


def _extract_indicators(source_code: str) -> list[str]:
    lowered = source_code.lower()
    indicators = []
    for label, aliases in INDICATOR_TERMS.items():
        if any(alias.lower() in lowered for alias in aliases):
            indicators.append(label)
    return indicators


def _extract_unique_terms(source_code: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term.lower() in source_code.lower()]


def _confidence_score(*groups) -> float:
    points = 0
    total = len(groups)
    for group in groups:
        if group:
            points += 1
    return round(min(0.95, max(0.15, points / total)), 2)


def _guess_title(functions: list[str]) -> str:
    if "handle_data" in functions and "before_trading_start" in functions:
        return "聚宽盘前选股与盘中交易策略"
    if "sell_open" in functions or "market_open" in functions:
        return "聚宽开盘交易策略"
    if functions:
        return "Python量化策略"
    return "量化策略片段"


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _dedupe(values: list[str], limit: int = 10) -> list[str]:
    seen = set()
    result = []
    for value in values:
        compacted = _compact(value)
        if compacted and compacted not in seen:
            seen.add(compacted)
            result.append(compacted)
        if len(result) >= limit:
            break
    return result
