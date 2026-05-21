"""
外部 cron / automation 调用入口。

示例：
python -m src.daily_picks_runner --strategy first_board_relay --date auto
"""

from __future__ import annotations

import argparse
import json

try:
    from daily_picks import DailyPickService, STRATEGY_KEYS
    from models import init_db
except ImportError:  # pragma: no cover - package execution fallback
    from .daily_picks import DailyPickService, STRATEGY_KEYS
    from .models import init_db


def parse_args():
    parser = argparse.ArgumentParser(description="Run daily strategy recommendations.")
    parser.add_argument(
        "--strategy",
        default="all",
        choices=[*STRATEGY_KEYS, "all"],
        help="Strategy to run. Use all for manual/debug runs.",
    )
    parser.add_argument(
        "--date",
        default="auto",
        help="Trade date in YYYY-MM-DD, or auto for latest trading day.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    init_db()
    date = None if args.date == "auto" else args.date
    result = DailyPickService().run(args.strategy, date)
    summary = {
        "trade_date": result.get("trade_date"),
        "strategy": args.strategy,
        "groups": [
            {
                "strategy_key": group.get("strategy_key"),
                "status": group.get("status"),
                "total_scanned": group.get("total_scanned"),
                "total_picks": group.get("total_picks"),
                "error": group.get("error"),
            }
            for group in result.get("groups", [])
            if args.strategy == "all" or group.get("strategy_key") == args.strategy
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
