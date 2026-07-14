"""
FastAPI入口
路由：认证、股票分析、自选股、分析历史
"""

import os
import traceback
import importlib
import hmac
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional

# ─── 模块导入 ─────────────────────────────────────────────

try:
    import models
    from models import (
        init_db, save_analysis, get_analysis_history, get_analysis_by_id,
        add_watchlist, get_watchlist, remove_watchlist, update_watchlist_pin, deduct_credits,
        get_daily_picks, get_local_sector_heat_ranking,
        list_user_quant_strategies, get_user_quant_strategy,
        create_user_quant_strategy, update_user_quant_strategy,
        update_user_quant_strategy_analysis, delete_user_quant_strategy
    )
    from auth import register_user, login_user, get_current_user
    from analyzer import analyze_stock
    from user_quant_strategies import analyze_quant_strategy_source
    from quant_strategy_ai import enhance_quant_strategy_analysis
    try:
        from market_heat import build_heat_context, build_market_heat_ranking, build_attention_heat_ranking
    except ImportError:  # pragma: no cover - optional provider in some deployments
        build_heat_context = None
        build_market_heat_ranking = None
        build_attention_heat_ranking = None
except ImportError:  # pragma: no cover - package execution fallback
    from . import models
    from .models import (
        init_db, save_analysis, get_analysis_history, get_analysis_by_id,
        add_watchlist, get_watchlist, remove_watchlist, update_watchlist_pin, deduct_credits,
        get_daily_picks, get_local_sector_heat_ranking,
        list_user_quant_strategies, get_user_quant_strategy,
        create_user_quant_strategy, update_user_quant_strategy,
        update_user_quant_strategy_analysis, delete_user_quant_strategy
    )
    from .auth import register_user, login_user, get_current_user
    from .analyzer import analyze_stock
    from .user_quant_strategies import analyze_quant_strategy_source
    from .quant_strategy_ai import enhance_quant_strategy_analysis
    try:
        from .market_heat import build_heat_context, build_market_heat_ranking, build_attention_heat_ranking
    except ImportError:  # pragma: no cover - optional provider in some deployments
        build_heat_context = None
        build_market_heat_ranking = None
        build_attention_heat_ranking = None


OPTIONAL_DEPENDENCY_ERRORS = {}


def _load_optional_module(module_name: str):
    qualified_name = f"{__package__}.{module_name}" if __package__ else module_name
    try:
        return importlib.import_module(qualified_name)
    except ImportError as exc:
        OPTIONAL_DEPENDENCY_ERRORS[module_name] = str(exc)
        return None


_data_fetcher_module = _load_optional_module("data_fetcher")
if _data_fetcher_module is None:
    get_stock_daily = None
    get_stock_info = None
    get_stock_minute = None
else:
    get_stock_daily = _data_fetcher_module.get_stock_daily
    get_stock_info = _data_fetcher_module.get_stock_info
    get_stock_minute = _data_fetcher_module.get_stock_minute

_daily_picks_module = _load_optional_module("daily_picks")
if _daily_picks_module is None:
    DailyPickService = None
    STRATEGY_KEYS = ()
else:
    DailyPickService = _daily_picks_module.DailyPickService
    STRATEGY_KEYS = _daily_picks_module.STRATEGY_KEYS

_research_notes_module = _load_optional_module("research_notes")
build_morning_note = _research_notes_module.build_morning_note if _research_notes_module else None

# ─── FastAPI App ──────────────────────────────────────────

MOUNT_PATH = "/a-stock"
PROXY_SECRET_HEADER = b"x-a-stock-proxy-secret"


class MountedProxyMiddleware:
    """Serve the app under /a-stock and optionally require the edge proxy."""

    def __init__(self, app, mount_path: str, proxy_secret: str = ""):
        self.app = app
        self.mount_path = mount_path.rstrip("/")
        self.proxy_secret = proxy_secret.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        if path == self.mount_path:
            response = RedirectResponse(f"{self.mount_path}/", status_code=308)
            await response(scope, receive, send)
            return

        mounted = path.startswith(f"{self.mount_path}/")
        effective_path = path[len(self.mount_path):] if mounted else path
        if self.proxy_secret and effective_path != "/healthz":
            supplied = next(
                (value for key, value in scope.get("headers", []) if key.lower() == PROXY_SECRET_HEADER),
                b"",
            )
            if not hmac.compare_digest(supplied, self.proxy_secret):
                response = JSONResponse({"error": "Forbidden", "code": 403}, status_code=403)
                await response(scope, receive, send)
                return

        if mounted:
            scope = dict(scope)
            scope["root_path"] = self.mount_path
            scope["path"] = effective_path or "/"
            scope["raw_path"] = scope["path"].encode("utf-8")
        await self.app(scope, receive, send)


app = FastAPI(title="A股股票分析系统", version="1.0.0", root_path=MOUNT_PATH)
app.add_middleware(
    MountedProxyMiddleware,
    mount_path=MOUNT_PATH,
    proxy_secret=os.getenv("A_STOCK_PROXY_SECRET", ""),
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ─── 启动事件 ─────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """启动时初始化数据库"""
    init_db()
    print("✅ 数据库初始化完成")


# ─── 请求模型 ─────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AddWatchlistRequest(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None


class PinWatchlistRequest(BaseModel):
    pinned: bool


class DailyPicksRunRequest(BaseModel):
    strategy: str = "all"
    date: Optional[str] = None


class QuantStrategyRequest(BaseModel):
    title: str
    platform: str = "python_joinquant"
    source_code: str


# ─── 响应格式 ─────────────────────────────────────────────

def success_response(data=None, message: str = "success"):
    return {"data": data, "message": message}


def error_response(error: str, code: int = 400):
    return JSONResponse(
        status_code=code,
        content={"error": error, "code": code}
    )


def optional_service_unavailable(module_name: str, service_name: str):
    detail = OPTIONAL_DEPENDENCY_ERRORS.get(module_name, "模块未加载")
    print(f"⚠️ {service_name}不可用: {detail}")
    return error_response(f"{service_name}暂不可用，请检查后端运行模块", 503)


def _validate_quant_strategy_request(req: QuantStrategyRequest) -> tuple[str, str, str]:
    title = (req.title or "").strip()
    platform = (req.platform or "python_joinquant").strip() or "python_joinquant"
    source_code = req.source_code or ""
    if not title:
        raise ValueError("策略名称不能为空")
    if len(title) > 120:
        raise ValueError("策略名称不能超过120个字符")
    if platform != "python_joinquant":
        raise ValueError("首版仅支持 Python/聚宽 策略")
    if not source_code.strip():
        raise ValueError("策略代码不能为空")
    if len(source_code.encode("utf-8")) > 300_000:
        raise ValueError("策略代码过长，请控制在300KB以内")
    return title, platform, source_code


def _analyze_quant_strategy_with_optional_ai(source_code: str, platform: str) -> tuple[str, dict, str, Optional[dict]]:
    parsed = analyze_quant_strategy_source(source_code, platform)
    parse_status = parsed["parse_status"]
    parse_result = parsed["parse_result"]
    ai_status = "disabled"
    ai_analysis = None
    try:
        enhanced = enhance_quant_strategy_analysis(source_code, parse_result, platform)
        if enhanced is not None:
            ai_status = "success"
            ai_analysis = enhanced
    except Exception as exc:
        ai_status = "failed"
        ai_analysis = {"error": str(exc)}
    return parse_status, parse_result, ai_status, ai_analysis


def _create_quant_strategy_record(user_id: int, req: QuantStrategyRequest) -> dict:
    title, platform, source_code = _validate_quant_strategy_request(req)
    parse_status, parse_result, ai_status, ai_analysis = _analyze_quant_strategy_with_optional_ai(source_code, platform)
    strategy_id = create_user_quant_strategy(
        user_id,
        title,
        platform,
        source_code,
        parse_status,
        parse_result,
        ai_status,
        ai_analysis,
    )
    return get_user_quant_strategy(user_id, strategy_id)


def _update_quant_strategy_record(user_id: int, strategy_id: int, req: QuantStrategyRequest) -> Optional[dict]:
    title, platform, source_code = _validate_quant_strategy_request(req)
    parse_status, parse_result, ai_status, ai_analysis = _analyze_quant_strategy_with_optional_ai(source_code, platform)
    updated = update_user_quant_strategy(
        user_id,
        strategy_id,
        title,
        platform,
        source_code,
        parse_status,
        parse_result,
        ai_status,
        ai_analysis,
    )
    if not updated:
        return None
    return get_user_quant_strategy(user_id, strategy_id)


def _reanalyze_quant_strategy_record(user_id: int, strategy_id: int) -> Optional[dict]:
    current = get_user_quant_strategy(user_id, strategy_id)
    if current is None:
        return None
    parse_status, parse_result, ai_status, ai_analysis = _analyze_quant_strategy_with_optional_ai(
        current["source_code"],
        current["platform"],
    )
    update_user_quant_strategy_analysis(user_id, strategy_id, parse_status, parse_result, ai_status, ai_analysis)
    return get_user_quant_strategy(user_id, strategy_id)


def _attention_result_to_market_compat(result: dict) -> dict:
    """Expose the capital-entry ranking through the legacy market heat shape."""
    compat_items = []
    for item in result.get("items") or []:
        compat = dict(item)
        compat.setdefault("sector_name", item.get("topic_name"))
        compat.setdefault("sector_type", item.get("topic_type"))
        compat.setdefault("sector_heat", item.get("entry_power_score"))
        compat.setdefault("sector_heat_score", item.get("entry_power_score"))
        compat.setdefault("heat", item.get("entry_power_score"))
        compat.setdefault("source", item.get("source") or "attention_heat_v2")
        compat_items.append(compat)
    compat_result = dict(result)
    compat_result["items"] = compat_items
    compat_result["source"] = result.get("source") or "attention_heat_v2"
    compat_result["model_version"] = "capital_entry_attention_v2"
    return compat_result


def format_trade_dates(df: pd.DataFrame) -> list:
    dates = []
    for _, row in df.tail(120).iterrows():
        date_value = row["date"]
        if hasattr(date_value, "strftime"):
            dates.append(date_value.strftime("%Y-%m-%d"))
        else:
            parsed = pd.to_datetime(date_value, errors="coerce")
            dates.append(parsed.strftime("%Y-%m-%d") if not pd.isna(parsed) else str(date_value))
    return dates


def build_heat_context_safely(code: str, df: pd.DataFrame, stock_info: dict, minute_df: Optional[pd.DataFrame]) -> Optional[dict]:
    """Best-effort sector heat enrichment; analysis must still work if the provider is unavailable."""
    if build_heat_context is None:
        return None
    try:
        return build_heat_context(code=code, stock_info=stock_info, daily_df=df, minute_df=minute_df)
    except TypeError:
        try:
            return build_heat_context(code, stock_info, df, minute_df)
        except Exception as exc:
            print(f"⚠️ 板块热度上下文获取失败: {exc}")
            return None
    except Exception as exc:
        print(f"⚠️ 板块热度上下文获取失败: {exc}")
        return None


def attach_sector_fields(result: dict, heat_context: Optional[dict]) -> None:
    """Expose sector heat fields when the provider returns them and analyzer did not already add them."""
    if not heat_context:
        return
    result.setdefault("heat_context", heat_context)
    board_hints = heat_context.get("board_ranking_hints") if isinstance(heat_context.get("board_ranking_hints"), dict) else {}
    sector_fields = {
        "sector_name": heat_context.get("sector_name"),
        "sector_type": heat_context.get("sector_type"),
        "sector_rank": heat_context.get("sector_rank") or heat_context.get("rank"),
        "sector_score": heat_context.get("sector_score") or heat_context.get("score") or heat_context.get("heat"),
        "sector_heat": heat_context.get("sector_heat") or heat_context.get("heat"),
        "sector_heat_score": heat_context.get("sector_heat_score") or heat_context.get("sector_heat") or heat_context.get("heat"),
        "sector_heat_window": heat_context.get("sector_heat_window") or heat_context.get("window"),
        "sector_heat_date": heat_context.get("sector_heat_date") or heat_context.get("date"),
        "sector_source": heat_context.get("sector_source") or heat_context.get("source"),
        "sector_confidence": heat_context.get("sector_confidence") or heat_context.get("confidence"),
        "sector_change_pct": heat_context.get("sector_change_pct") or board_hints.get("change_pct"),
        "sector_context": heat_context,
    }
    for key, value in sector_fields.items():
        if value not in (None, "") and key not in result:
            result[key] = value


def run_stock_analysis(
    code: str,
    cost_price: Optional[float],
    buy_date: Optional[str],
    current_user: dict,
) -> dict:
    """Run the synchronous analysis pipeline away from the async event loop."""
    if not all((get_stock_daily, get_stock_info, get_stock_minute)):
        raise RuntimeError("行情数据模块未加载")
    df = get_stock_daily(code, days=120)
    stock_info = get_stock_info(code)
    minute_df, minute_error = get_stock_minute(code)
    heat_context = build_heat_context_safely(code, df, stock_info, minute_df)
    if heat_context:
        stock_info["heat_context"] = heat_context

    result = analyze_stock(
        df,
        stock_info,
        code,
        minute_df=minute_df,
        cost_price=cost_price,
        minute_error=minute_error,
        buy_date=buy_date,
    )
    attach_sector_fields(result, heat_context)

    kline_records = []
    for _, row in df.tail(120).iterrows():
        kline_records.append({
            "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0
        })
    result["kline_data"] = kline_records
    result["buy_date"] = buy_date
    result["cost_price"] = cost_price

    deduct_credits(current_user["id"], 1)
    analysis_id = save_analysis(current_user["id"], result)
    result["id"] = analysis_id
    result["credits_remaining"] = current_user["credits"] - 1
    return result


# ─── 全局异常处理 ─────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"error": f"服务器内部错误: {str(exc)}", "code": 500}
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """业务逻辑错误处理"""
    return JSONResponse(
        status_code=400,
        content={"error": str(exc), "code": 400}
    )


# ─── 首页 ─────────────────────────────────────────────────

@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}

@app.get("/")
async def index():
    """返回前端页面"""
    index_path = os.path.join(TEMPLATE_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return success_response({"message": "A股股票分析系统 API v1.0"})


# ─── 认证路由 ─────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    """用户注册"""
    try:
        user = await run_in_threadpool(register_user, req.username, req.email, req.password)
        return success_response(user, "注册成功")
    except ValueError as e:
        return error_response(str(e), 400)


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """用户登录"""
    try:
        result = await run_in_threadpool(login_user, req.username, req.password)
        return success_response(result, "登录成功")
    except ValueError as e:
        return error_response(str(e), 401)


@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """获取当前用户信息"""
    return success_response(current_user)


# ─── 股票分析路由 ─────────────────────────────────────────

@app.get("/api/stock/{code}")
async def analyze_stock_api(
    code: str,
    cost_price: Optional[float] = None,
    buy_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    分析股票（核心功能）
    每次分析扣1个credits
    """
    if not all((get_stock_daily, get_stock_info, get_stock_minute)):
        return optional_service_unavailable("data_fetcher", "股票分析服务")
    # 检查credits
    if current_user["credits"] <= 0:
        return error_response("credits不足，请充值", 403)
    
    try:
        result = await run_in_threadpool(
            run_stock_analysis,
            code,
            cost_price,
            buy_date,
            current_user,
        )
        return success_response(result, "分析完成")
        
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 502)
    except Exception as e:
        traceback.print_exc()
        return error_response(f"分析失败: {str(e)}", 500)


@app.get("/api/stock/{code}/trade-dates")
async def stock_trade_dates_api(
    code: str,
    current_user: dict = Depends(get_current_user)
):
    """
    获取股票最近120个交易日
    不扣除credits
    """
    if get_stock_daily is None:
        return optional_service_unavailable("data_fetcher", "交易日服务")
    try:
        df = await run_in_threadpool(get_stock_daily, code, days=120)
        trade_dates = format_trade_dates(df)
        return success_response({
            "stock_code": code,
            "trade_dates": trade_dates,
            "latest_trade_date": trade_dates[-1] if trade_dates else None,
        })
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 502)
    except Exception as e:
        traceback.print_exc()
        return error_response(f"获取交易日失败: {str(e)}", 500)


# ─── 分析历史 ─────────────────────────────────────────────

@app.get("/api/history")
async def history_api(
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """获取我的分析历史"""
    records = await run_in_threadpool(get_analysis_history, current_user["id"], limit)
    return success_response(records)


# ─── 每日策略推荐 ─────────────────────────────────────────

@app.get("/api/research/morning-note")
async def morning_note_api(
    date: Optional[str] = None,
    limit: int = 5,
    current_user: dict = Depends(get_current_user)
):
    """生成盘前晨会稿；仅作研究参考，不持久化。"""
    if build_morning_note is None:
        return optional_service_unavailable("research_notes", "晨会稿服务")
    warnings = []
    daily_picks = {}
    heat_ranking = {}

    try:
        daily_picks = await run_in_threadpool(get_daily_picks, date)
    except Exception as e:
        traceback.print_exc()
        warnings.append(f"今日推荐读取失败: {str(e)}")

    if build_attention_heat_ranking is None:
        warnings.append("attention heat provider 未接入")
        heat_ranking = {
            "items": [],
            "source": "unavailable",
            "window": "today+snapshot",
            "confidence": 0.0,
            "data_status": {
                "status": "degraded",
                "warnings": ["attention heat provider 未接入"],
            },
        }
    else:
        try:
            safe_limit = max(1, min(int(limit or 5), 10))
            heat_ranking = await run_in_threadpool(build_attention_heat_ranking, limit=safe_limit, leaders=3)
        except Exception as e:
            traceback.print_exc()
            warning = f"资金进入热榜读取失败: {str(e)}"
            warnings.append(warning)
            heat_ranking = {
                "items": [],
                "source": "unavailable",
                "window": "today+snapshot",
                "confidence": 0.0,
                "data_status": {
                    "status": "degraded",
                    "warnings": [warning],
                },
            }

    if warnings:
        heat_ranking.setdefault("data_status", {})
        heat_warnings = heat_ranking["data_status"].get("warnings") or []
        heat_ranking["data_status"]["warnings"] = [*warnings, *heat_warnings]
        heat_ranking["data_status"]["status"] = "degraded"

    note = await run_in_threadpool(build_morning_note, daily_picks, heat_ranking, date)
    return success_response(note)


@app.get("/api/daily-picks")
async def daily_picks_api(
    date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """读取每日三策略推荐结果。"""
    return success_response(await run_in_threadpool(get_daily_picks, date))


@app.get("/api/attention-heat/ranking")
async def attention_heat_ranking_api(
    limit: int = 5,
    leaders: int = 3,
    current_user: dict = Depends(get_current_user)
):
    """读取按资金进入体量 + 外部散户流量增速排序的题材榜。"""
    if build_attention_heat_ranking is None:
        return success_response({
            "items": [],
            "source": "unavailable",
            "window": "today+snapshot",
            "confidence": 0.0,
            "data_status": {
                "status": "degraded",
                "douyin_status": "unavailable_public_not_integrated",
                "warnings": ["attention heat provider 未接入"],
            },
        })
    try:
        result = await run_in_threadpool(build_attention_heat_ranking, limit=limit, leaders=leaders)
        return success_response(result)
    except Exception as e:
        traceback.print_exc()
        return success_response({
            "items": [],
            "source": "unavailable",
            "window": "today+snapshot",
            "confidence": 0.0,
            "data_status": {
                "status": "degraded",
                "eastmoney_fund_status": "unavailable",
                "ths_fund_status": "unavailable",
                "eastmoney_attention_status": "unavailable",
                "xueqiu_status": "unavailable",
                "douyin_status": "unavailable_public_not_integrated",
                "snapshot_status": "unknown",
                "warnings": [f"资金进入热榜获取失败: {str(e)}"],
            },
        })


@app.get("/api/market-heat/ranking")
async def market_heat_ranking_api(
    limit: int = 20,
    current_user: dict = Depends(get_current_user)
):
    """读取今日板块/题材热度总排行榜。"""
    if build_attention_heat_ranking is not None:
        try:
            result = await run_in_threadpool(build_attention_heat_ranking, limit=limit, leaders=3)
            if result.get("items"):
                return success_response(_attention_result_to_market_compat(result))
        except Exception:
            traceback.print_exc()
    if build_market_heat_ranking is None:
        return success_response({
            "items": [],
            "source": "unavailable",
            "window": "latest_proxy",
            "confidence": 0.0,
            "data_status": {
                "status": "degraded",
                "warnings": ["market_heat provider 未接入"],
            },
        })
    try:
        result = await run_in_threadpool(build_market_heat_ranking, limit=limit)
        if not result.get("items"):
            fallback = await run_in_threadpool(get_local_sector_heat_ranking, limit)
            if fallback.get("items"):
                warnings = []
                status = result.get("data_status") if isinstance(result.get("data_status"), dict) else {}
                warnings.extend(status.get("warnings") or [])
                fallback_status = fallback.get("data_status") if isinstance(fallback.get("data_status"), dict) else {}
                warnings.extend(fallback_status.get("warnings") or [])
                fallback.setdefault("data_status", {})
                fallback["data_status"]["warnings"] = warnings
                fallback["data_status"]["status"] = "fallback"
                return success_response(fallback)
        return success_response(result)
    except Exception as e:
        traceback.print_exc()
        fallback = await run_in_threadpool(get_local_sector_heat_ranking, limit)
        if fallback.get("items"):
            fallback.setdefault("data_status", {})
            warnings = fallback["data_status"].get("warnings") or []
            fallback["data_status"]["warnings"] = [f"东财热度排行榜获取失败: {str(e)}", *warnings]
            fallback["data_status"]["status"] = "fallback"
            return success_response(fallback)
        return success_response({
            "items": [],
            "source": "unavailable",
            "window": "latest_proxy",
            "confidence": 0.0,
            "data_status": {
                "status": "degraded",
                "warnings": [f"热度排行榜获取失败: {str(e)}"],
            },
        })


@app.post("/api/daily-picks/run")
async def run_daily_picks_api(
    req: DailyPicksRunRequest,
    current_user: dict = Depends(get_current_user)
):
    """手动触发策略推荐运行，用于调试和页面刷新。"""
    if DailyPickService is None:
        return optional_service_unavailable("daily_picks", "策略推荐运行服务")
    allowed = set(STRATEGY_KEYS) | {"all"}
    if req.strategy not in allowed:
        return error_response("未知策略", 400)
    try:
        service = DailyPickService()
        result = await run_in_threadpool(service.run, req.strategy, req.date)
        return success_response(result, "策略推荐已更新")
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        traceback.print_exc()
        return error_response(f"策略推荐运行失败: {str(e)}", 500)


# ─── 用户量化策略库 ───────────────────────────────────────

@app.get("/api/quant-strategies")
async def list_quant_strategies_api(current_user: dict = Depends(get_current_user)):
    """读取当前用户保存的量化策略。"""
    items = await run_in_threadpool(list_user_quant_strategies, current_user["id"])
    return success_response(items)


@app.post("/api/quant-strategies")
async def create_quant_strategy_api(
    req: QuantStrategyRequest,
    current_user: dict = Depends(get_current_user)
):
    """新增量化策略并立即做静态解析。"""
    try:
        item = await run_in_threadpool(_create_quant_strategy_record, current_user["id"], req)
        return success_response(item, "策略已保存并解析")
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        traceback.print_exc()
        return error_response(f"量化策略保存失败: {str(e)}", 500)


@app.get("/api/quant-strategies/{strategy_id}")
async def get_quant_strategy_api(
    strategy_id: int,
    current_user: dict = Depends(get_current_user)
):
    """读取单条量化策略详情。"""
    item = await run_in_threadpool(get_user_quant_strategy, current_user["id"], strategy_id)
    if item is None:
        return error_response("量化策略不存在", 404)
    return success_response(item)


@app.put("/api/quant-strategies/{strategy_id}")
async def update_quant_strategy_api(
    strategy_id: int,
    req: QuantStrategyRequest,
    current_user: dict = Depends(get_current_user)
):
    """更新量化策略并重新解析。"""
    try:
        item = await run_in_threadpool(_update_quant_strategy_record, current_user["id"], strategy_id, req)
        if item is None:
            return error_response("量化策略不存在", 404)
        return success_response(item, "策略已更新并解析")
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        traceback.print_exc()
        return error_response(f"量化策略更新失败: {str(e)}", 500)


@app.post("/api/quant-strategies/{strategy_id}/analyze")
async def analyze_quant_strategy_api(
    strategy_id: int,
    current_user: dict = Depends(get_current_user)
):
    """重新解析一条已保存的量化策略。"""
    try:
        item = await run_in_threadpool(_reanalyze_quant_strategy_record, current_user["id"], strategy_id)
        if item is None:
            return error_response("量化策略不存在", 404)
        return success_response(item, "策略已重新解析")
    except Exception as e:
        traceback.print_exc()
        return error_response(f"量化策略解析失败: {str(e)}", 500)


@app.delete("/api/quant-strategies/{strategy_id}")
async def delete_quant_strategy_api(
    strategy_id: int,
    current_user: dict = Depends(get_current_user)
):
    """删除当前用户的一条量化策略。"""
    success = await run_in_threadpool(delete_user_quant_strategy, current_user["id"], strategy_id)
    if success:
        return success_response(None, "策略已删除")
    return error_response("量化策略不存在", 404)


# ─── 自选股 ───────────────────────────────────────────────

@app.get("/api/watchlist")
async def get_watchlist_api(current_user: dict = Depends(get_current_user)):
    """获取自选股列表"""
    items = await run_in_threadpool(get_watchlist, current_user["id"])
    return success_response(items)


@app.post("/api/watchlist")
async def add_watchlist_api(
    req: AddWatchlistRequest,
    current_user: dict = Depends(get_current_user)
):
    """添加自选股"""
    await run_in_threadpool(add_watchlist, current_user["id"], req.stock_code, req.stock_name)
    return success_response(None, "添加成功")


@app.delete("/api/watchlist/{code}")
async def remove_watchlist_api(
    code: str,
    current_user: dict = Depends(get_current_user)
):
    """删除自选股"""
    success = await run_in_threadpool(remove_watchlist, current_user["id"], code)
    if success:
        return success_response(None, "删除成功")
    return error_response("自选股不存在", 404)


@app.patch("/api/watchlist/{code}/pin")
async def update_watchlist_pin_api(
    code: str,
    req: PinWatchlistRequest,
    current_user: dict = Depends(get_current_user)
):
    """置顶或取消置顶自选股"""
    success = await run_in_threadpool(update_watchlist_pin, current_user["id"], code, req.pinned)
    if success:
        return success_response(None, "置顶成功" if req.pinned else "已取消置顶")
    return error_response("自选股不存在", 404)


# ─── 启动入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
