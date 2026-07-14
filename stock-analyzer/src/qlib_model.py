"""
Offline Qlib-style model training and prediction for the daily picks page.

The FastAPI app must be able to import without pyqlib/lightgbm installed. This
module therefore keeps optional ML dependencies behind explicit runtime checks
and uses a small deterministic linear backend for local tests and smoke runs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import a_stock_data_provider as a_stock_data
except ImportError:  # pragma: no cover - package execution fallback
    try:
        from . import a_stock_data_provider as a_stock_data
    except ImportError:  # pragma: no cover
        a_stock_data = None


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_QLIB_DATA_DIR = ROOT_DIR / "data" / "qlib_data"
DEFAULT_ARTIFACT_DIR = ROOT_DIR / "data" / "qlib_models"
MODEL_VERSION = "qlib_offline_linear_v1"
FEATURE_COLUMNS = [
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "ma5_ratio",
    "ma10_ratio",
    "volume_ma5_ratio",
    "intraday_range",
    "amount_ma5_ratio",
]


class QlibModelUnavailable(RuntimeError):
    """Raised when the requested Qlib backend cannot run in this environment."""


def _normalize_code(code: str) -> str:
    value = str(code or "").strip().upper()
    if "." in value:
        value = value.split(".")[0]
    for prefix in ("SH", "SZ", "BJ"):
        value = value.replace(prefix, "")
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits.zfill(6)[-6:]


def _is_base_stock_code(code: str) -> bool:
    plain = _normalize_code(code)
    if plain.startswith(("3", "4", "8", "9", "688")):
        return False
    return len(plain) == 6


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        value = float(value)
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    return default


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_feature_frame(code: str, name: str, history: pd.DataFrame) -> pd.DataFrame:
    """Build daily OHLCV features and a future 3-trading-day return label."""
    if history is None or history.empty:
        return pd.DataFrame()
    df = history.copy()
    rename = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount"}
    df = df.rename(columns=rename)
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return pd.DataFrame()
    if "amount" not in df.columns:
        df["amount"] = pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume"]).sort_values("date").reset_index(drop=True)
    if len(df) < 8:
        return pd.DataFrame()

    close = df["close"]
    volume = df["volume"]
    amount = df["amount"]
    features = pd.DataFrame(
        {
            "code": _normalize_code(code),
            "name": name or _normalize_code(code),
            "date": df["date"].dt.strftime("%Y-%m-%d"),
            "close": close,
            "ret_1d": close.pct_change(1).fillna(0),
            "ret_3d": close.pct_change(3).fillna(0),
            "ret_5d": close.pct_change(5).fillna(0),
            "ma5_ratio": close / close.rolling(5, min_periods=1).mean() - 1,
            "ma10_ratio": close / close.rolling(10, min_periods=1).mean() - 1,
            "volume_ma5_ratio": volume / volume.rolling(5, min_periods=1).mean() - 1,
            "intraday_range": (df["high"] - df["low"]) / close,
            "amount_ma5_ratio": amount / amount.rolling(5, min_periods=1).mean() - 1,
            "label": close.shift(-3) / close - 1,
        }
    )
    return features.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)


def _cut_history_at(history: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    df = history.copy()
    if "date" not in df.columns and "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    cutoff = pd.to_datetime(trade_date, errors="coerce")
    if pd.isna(cutoff):
        return df.dropna(subset=["date"])
    return df[df["date"] <= cutoff].dropna(subset=["date"]).reset_index(drop=True)


class AStockDataQlibProvider:
    """a-stock-data-first provider for the offline model runner."""

    def __init__(self):
        self._spot_cache = None

    def _ak(self):
        import akshare as ak
        return ak

    def normalize_trade_date(self, trade_date: Optional[str] = None) -> str:
        if trade_date and trade_date != "auto":
            return trade_date
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if a_stock_data is not None:
            try:
                dates = a_stock_data.get_trade_dates(end_date=today, days=260)
                eligible = [date for date in dates if date <= today]
                if eligible:
                    return eligible[-1]
            except Exception:
                pass
        try:
            df = self._ak().tool_trade_date_hist_sina()
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            dates = sorted(pd.to_datetime(df[col], errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist())
            eligible = [date for date in dates if date <= today]
            return eligible[-1] if eligible else today
        except Exception:
            return today

    def stock_universe(self, trade_date: str) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()

        def add_item(code_value, name_value):
            code = _normalize_code(code_value)
            name = str(name_value or code)
            if not _is_base_stock_code(code):
                return
            if "ST" in name.upper() or "退" in name:
                return
            if code in seen:
                return
            seen.add(code)
            rows.append({"code": code, "name": name})

        if a_stock_data is not None:
            try:
                for item in a_stock_data.get_limit_up_candidates(trade_date) or []:
                    add_item(item.get("code"), item.get("name"))
            except Exception:
                pass
            try:
                for item in a_stock_data.get_fund_flow_stocks(limit=300) or []:
                    add_item(item.get("code"), item.get("name"))
            except Exception:
                pass
            if len(rows) >= 30:
                return rows[:300]

        if self._spot_cache is None:
            self._spot_cache = self._ak().stock_zh_a_spot_em()
        df = self._spot_cache if self._spot_cache is not None else pd.DataFrame()
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            code = _normalize_code(row.get("代码"))
            name = str(row.get("名称") or code)
            if not _is_base_stock_code(code):
                continue
            if "ST" in name.upper() or "退" in name:
                continue
            rows.append({"code": code, "name": name})
            if len(rows) >= 300:
                break
        return rows

    def stock_history(self, code: str, end_date: str, days: int) -> pd.DataFrame:
        try:
            try:
                from data_fetcher import get_stock_daily
            except ImportError:  # pragma: no cover - package import fallback
                from .data_fetcher import get_stock_daily
            return get_stock_daily(_normalize_code(code), days=days)
        except Exception:
            return pd.DataFrame()


AkshareQlibProvider = AStockDataQlibProvider


@dataclass
class QlibModelPipeline:
    provider: object | None = None
    artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR
    qlib_data_dir: Path | str = DEFAULT_QLIB_DATA_DIR
    require_external_backend: bool = False
    history_days: int = 90

    def __post_init__(self):
        self.provider = self.provider or AStockDataQlibProvider()
        self.artifact_dir = Path(self.artifact_dir)
        self.qlib_data_dir = Path(self.qlib_data_dir)

    def _ensure_backend_available(self) -> None:
        if not self.require_external_backend:
            return
        try:
            import qlib  # noqa: F401
            import lightgbm  # noqa: F401
        except Exception as exc:
            raise QlibModelUnavailable(f"pyqlib/lightgbm backend unavailable: {exc}") from exc

    def _collect_features(self, trade_date: str) -> pd.DataFrame:
        rows = []
        for item in self.provider.stock_universe(trade_date):
            code = _normalize_code(item.get("code"))
            name = item.get("name") or code
            if not _is_base_stock_code(code):
                continue
            hist = self.provider.stock_history(code, trade_date, self.history_days)
            hist = _cut_history_at(hist, trade_date)
            frame = build_feature_frame(code, name, hist)
            if not frame.empty:
                rows.append(frame)
        if not rows:
            return pd.DataFrame()
        return pd.concat(rows, ignore_index=True)

    def train(self, trade_date: str = "auto") -> dict:
        self._ensure_backend_available()
        resolved_date = self.provider.normalize_trade_date(trade_date)
        frame = self._collect_features(resolved_date)
        train_frame = frame.dropna(subset=["label"]).copy()
        if train_frame.empty or train_frame["code"].nunique() < 2:
            raise QlibModelUnavailable("Qlib训练数据不足，无法生成样本外模型")
        x = train_frame[FEATURE_COLUMNS].astype(float).to_numpy()
        y = train_frame["label"].astype(float).to_numpy()
        x_mean = x.mean(axis=0)
        x_std = x.std(axis=0)
        x_std[x_std == 0] = 1.0
        design = np.column_stack([np.ones(len(x)), (x - x_mean) / x_std])
        weights = np.linalg.pinv(design) @ y
        model = {
            "model_version": MODEL_VERSION,
            "feature_columns": FEATURE_COLUMNS,
            "intercept": float(weights[0]),
            "weights": [float(value) for value in weights[1:]],
            "x_mean": [float(value) for value in x_mean],
            "x_std": [float(value) for value in x_std],
            "train_start": str(train_frame["date"].min()),
            "train_end": str(train_frame["date"].max()),
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "sample_count": int(len(train_frame)),
            "instrument_count": int(train_frame["code"].nunique()),
            "backend": "qlib_offline_linear",
        }
        _write_json(self.artifact_dir / "latest_model.json", model)
        return model

    def _load_or_train_model(self, trade_date: str) -> dict:
        path = self.artifact_dir / "latest_model.json"
        if path.exists():
            model = _read_json(path)
            train_end = pd.to_datetime(model.get("train_end"), errors="coerce")
            predict_date = pd.to_datetime(trade_date, errors="coerce")
            if not pd.isna(train_end) and not pd.isna(predict_date) and train_end <= predict_date:
                return model
        return self.train(trade_date)

    def predict(self, trade_date: str = "auto", top_n: int = 10) -> dict:
        self._ensure_backend_available()
        resolved_date = self.provider.normalize_trade_date(trade_date)
        model = self._load_or_train_model(resolved_date)
        frame = self._collect_features(resolved_date)
        if frame.empty:
            raise QlibModelUnavailable("Qlib预测数据不足，无法生成推荐")
        latest = frame.sort_values(["code", "date"]).groupby("code", as_index=False).tail(1).copy()
        if latest.empty:
            raise QlibModelUnavailable("Qlib预测截面为空")
        x = latest[model["feature_columns"]].astype(float).to_numpy()
        x_mean = np.array(model["x_mean"], dtype=float)
        x_std = np.array(model["x_std"], dtype=float)
        weights = np.array(model["weights"], dtype=float)
        pred = model["intercept"] + ((x - x_mean) / x_std) @ weights
        latest["pred_return_3d"] = pred
        min_pred = float(latest["pred_return_3d"].min())
        max_pred = float(latest["pred_return_3d"].max())
        if math.isclose(min_pred, max_pred):
            latest["model_score"] = 50.0
        else:
            latest["model_score"] = (latest["pred_return_3d"] - min_pred) / (max_pred - min_pred) * 100
        total_scanned = int(len(latest))
        latest = latest.sort_values(["model_score", "pred_return_3d"], ascending=False).head(top_n).reset_index(drop=True)
        recommendations = []
        for idx, row in latest.iterrows():
            model_score = round(_safe_float(row["model_score"]), 2)
            pred_return = round(_safe_float(row["pred_return_3d"]), 6)
            code = _normalize_code(row["code"])
            name = row.get("name") or code
            recommendations.append(
                {
                    "strategy_key": "qlib_model",
                    "strategy_name": "AI模型选股",
                    "code": code,
                    "name": name,
                    "rank": idx + 1,
                    "score": model_score,
                    "mode": "Qlib样本外预测",
                    "reasons": [f"Qlib模型预测未来3日收益为{pred_return:.2%}", "模型分位于当日候选池前列"],
                    "risks": ["模型输出仅作量化研究参考，不构成投资建议", "训练样本、公开数据源和市场风格切换都可能导致预测失效"],
                    "metrics": {
                        "model_score": model_score,
                        "pred_return_3d": pred_return,
                        "model_version": model.get("model_version", MODEL_VERSION),
                        "train_start": model.get("train_start"),
                        "train_end": model.get("train_end"),
                        "predict_date": resolved_date,
                    },
                    "data_status": {"status": "ok", "qlib_status": "ok", "warnings": []},
                }
            )
        result = {
            "strategy_key": "qlib_model",
            "trade_date": resolved_date,
            "status": "success",
            "total_scanned": total_scanned,
            "total_picks": len(recommendations),
            "recommendations": recommendations,
            "error": None,
            "model": model,
        }
        _write_json(
            self.artifact_dir / f"predictions_{resolved_date}.json",
            {key: value for key, value in result.items() if key != "model"},
        )
        return result

    def run(self, trade_date: str = "auto", top_n: int = 10) -> dict:
        resolved_date = self.provider.normalize_trade_date(trade_date)
        self.train(resolved_date)
        return self.predict(resolved_date, top_n=top_n)


def failed_qlib_result(trade_date: str, error: Exception | str) -> dict:
    message = str(error)[:240]
    return {
        "strategy_key": "qlib_model",
        "trade_date": trade_date,
        "status": "failed",
        "total_scanned": 0,
        "total_picks": 0,
        "recommendations": [],
        "error": message,
        "data_status": {"status": "failed", "qlib_status": "failed", "warnings": [message]},
    }


def run_qlib_model(
    mode: str = "run",
    trade_date: str = "auto",
    provider=None,
    artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR,
    qlib_data_dir: Path | str = DEFAULT_QLIB_DATA_DIR,
    top_n: int = 10,
    require_external_backend: bool = False,
    raise_on_unavailable: bool = True,
) -> dict:
    pipeline = QlibModelPipeline(
        provider=provider,
        artifact_dir=artifact_dir,
        qlib_data_dir=qlib_data_dir,
        require_external_backend=require_external_backend,
    )
    resolved_date = pipeline.provider.normalize_trade_date(trade_date)
    try:
        if mode == "train":
            model = pipeline.train(resolved_date)
            return {
                "strategy_key": "qlib_model",
                "trade_date": resolved_date,
                "status": "success",
                "total_scanned": model["sample_count"],
                "total_picks": 0,
                "recommendations": [],
                "error": None,
                "model": model,
            }
        if mode == "predict":
            return pipeline.predict(resolved_date, top_n=top_n)
        if mode == "run":
            return pipeline.run(resolved_date, top_n=top_n)
        raise ValueError(f"未知Qlib运行模式: {mode}")
    except QlibModelUnavailable as exc:
        if raise_on_unavailable:
            raise
        return failed_qlib_result(resolved_date, exc)
