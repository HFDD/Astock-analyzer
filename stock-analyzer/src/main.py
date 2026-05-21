"""
FastAPI入口
路由：认证、股票分析、自选股、分析历史
"""

import os
import traceback
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# ─── 模块导入 ─────────────────────────────────────────────

try:
    import models
    from models import (
        init_db, save_analysis, get_analysis_history, get_analysis_by_id,
        add_watchlist, get_watchlist, remove_watchlist, deduct_credits,
        get_daily_picks
    )
    from auth import register_user, login_user, get_current_user
    from data_fetcher import get_stock_daily, get_stock_info, get_stock_minute
    from analyzer import analyze_stock
    from daily_picks import DailyPickService, STRATEGY_KEYS
except ImportError:  # pragma: no cover - package execution fallback
    from . import models
    from .models import (
        init_db, save_analysis, get_analysis_history, get_analysis_by_id,
        add_watchlist, get_watchlist, remove_watchlist, deduct_credits,
        get_daily_picks
    )
    from .auth import register_user, login_user, get_current_user
    from .data_fetcher import get_stock_daily, get_stock_info, get_stock_minute
    from .analyzer import analyze_stock
    from .daily_picks import DailyPickService, STRATEGY_KEYS

# ─── FastAPI App ──────────────────────────────────────────

app = FastAPI(title="A股股票分析系统", version="1.0.0")

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


class DailyPicksRunRequest(BaseModel):
    strategy: str = "all"
    date: Optional[str] = None


# ─── 响应格式 ─────────────────────────────────────────────

def success_response(data=None, message: str = "success"):
    return {"data": data, "message": message}


def error_response(error: str, code: int = 400):
    return JSONResponse(
        status_code=code,
        content={"error": error, "code": code}
    )


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
        user = register_user(req.username, req.email, req.password)
        return success_response(user, "注册成功")
    except ValueError as e:
        return error_response(str(e), 400)


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """用户登录"""
    try:
        result = login_user(req.username, req.password)
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
    # 检查credits
    if current_user["credits"] <= 0:
        return error_response("credits不足，请充值", 403)
    
    try:
        # 获取数据
        df = get_stock_daily(code, days=120)
        stock_info = get_stock_info(code)
        minute_df, minute_error = get_stock_minute(code)
        
        # 分析
        result = analyze_stock(
            df,
            stock_info,
            code,
            minute_df=minute_df,
            cost_price=cost_price,
            minute_error=minute_error,
            buy_date=buy_date,
        )
        
        # 添加K线数据（前端画图用）
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
        
        # 扣除credits
        deduct_credits(current_user["id"], 1)
        
        # 保存分析记录
        analysis_id = save_analysis(current_user["id"], result)
        result["id"] = analysis_id
        result["credits_remaining"] = current_user["credits"] - 1
        
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
    try:
        df = get_stock_daily(code, days=120)
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
    records = get_analysis_history(current_user["id"], limit)
    return success_response(records)


# ─── 每日策略推荐 ─────────────────────────────────────────

@app.get("/api/daily-picks")
async def daily_picks_api(
    date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """读取每日三策略推荐结果。"""
    return success_response(get_daily_picks(date))


@app.post("/api/daily-picks/run")
async def run_daily_picks_api(
    req: DailyPicksRunRequest,
    current_user: dict = Depends(get_current_user)
):
    """手动触发策略推荐运行，用于调试和页面刷新。"""
    allowed = set(STRATEGY_KEYS) | {"all"}
    if req.strategy not in allowed:
        return error_response("未知策略", 400)
    try:
        result = DailyPickService().run(req.strategy, req.date)
        return success_response(result, "策略推荐已更新")
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        traceback.print_exc()
        return error_response(f"策略推荐运行失败: {str(e)}", 500)


# ─── 自选股 ───────────────────────────────────────────────

@app.get("/api/watchlist")
async def get_watchlist_api(current_user: dict = Depends(get_current_user)):
    """获取自选股列表"""
    items = get_watchlist(current_user["id"])
    return success_response(items)


@app.post("/api/watchlist")
async def add_watchlist_api(
    req: AddWatchlistRequest,
    current_user: dict = Depends(get_current_user)
):
    """添加自选股"""
    add_watchlist(current_user["id"], req.stock_code, req.stock_name)
    return success_response(None, "添加成功")


@app.delete("/api/watchlist/{code}")
async def remove_watchlist_api(
    code: str,
    current_user: dict = Depends(get_current_user)
):
    """删除自选股"""
    success = remove_watchlist(current_user["id"], code)
    if success:
        return success_response(None, "删除成功")
    return error_response("自选股不存在", 404)


# ─── 启动入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
