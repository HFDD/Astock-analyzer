"""
Command line entrypoint for the offline Qlib model group.

Examples:
python3 -m src.qlib_model_runner --mode run --date auto
python3 -m src.qlib_model_runner --mode train --date 2026-05-22
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from models import init_db, save_strategy_run_results
    from qlib_model import run_qlib_model
except ImportError:  # pragma: no cover - package execution fallback
    from .models import init_db, save_strategy_run_results
    from .qlib_model import run_qlib_model


def parse_args():
    parser = argparse.ArgumentParser(description="Run offline Qlib model training and prediction.")
    parser.add_argument("--mode", choices=["train", "predict", "run"], default="run", help="Qlib pipeline mode.")
    parser.add_argument("--date", default="auto", help="Trade date in YYYY-MM-DD, or auto for latest trading day.")
    parser.add_argument("--top-n", type=int, default=10, help="Maximum recommendations to save in predict/run modes.")
    parser.add_argument(
        "--require-external-backend",
        action="store_true",
        help="Require pyqlib/lightgbm imports instead of the local deterministic fallback backend.",
    )
    return parser.parse_args()


def _summary(result: dict, mode: str) -> dict:
    return {
        "strategy_key": "qlib_model",
        "mode": mode,
        "trade_date": result.get("trade_date"),
        "status": result.get("status"),
        "total_scanned": result.get("total_scanned"),
        "total_picks": result.get("total_picks"),
        "error": result.get("error"),
    }


def main() -> int:
    args = parse_args()
    init_db()
    date = None if args.date == "auto" else args.date
    result = run_qlib_model(
        mode=args.mode,
        trade_date=date,
        top_n=args.top_n,
        require_external_backend=args.require_external_backend,
        raise_on_unavailable=False,
    )
    if args.mode in {"predict", "run"}:
        save_strategy_run_results(
            "qlib_model",
            result.get("trade_date"),
            result.get("status", "failed"),
            int(result.get("total_scanned") or 0),
            result.get("recommendations") or [],
            error=result.get("error"),
        )
    print(json.dumps(_summary(result, args.mode), ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())

