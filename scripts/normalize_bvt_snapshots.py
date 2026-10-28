"""Create a continuous MOVENT input set from BVT replay plus compact history.

Replay observations remain authoritative. A ticker absent from an otherwise valid
session is restored from BVT's same-session compact dollar-volume history. The
compact value is stored in floored USD millions, so its interval midpoint is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-data", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    latest = json.loads(args.latest_data.read_text(encoding="utf-8"))
    history = json.loads(args.history.read_text(encoding="utf-8"))
    metadata = {str(row.get("t") or "").upper(): row for row in latest.get("stocks", []) if row.get("t")}
    args.out.mkdir(parents=True, exist_ok=True)
    repaired_total = 0

    for index, trading_date in enumerate(history["dates"]):
        source_path = args.snapshots / f"{trading_date}.json"
        payload = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else {
            "schema_version": 2, "trading_date": trading_date, "tickers": {}
        }
        tickers = payload.setdefault("tickers", {})
        repaired = 0
        for ticker, series in history.get("stocks", {}).items():
            if ticker in tickers or ticker not in metadata or len(series) <= index:
                continue
            compact_millions = series[index]
            if not isinstance(compact_millions, (int, float)) or compact_millions < 0:
                continue
            row = metadata[ticker]
            tickers[ticker] = {
                "name": row.get("n") or ticker,
                "name_ko": row.get("nko") or "",
                "close_price": None,
                "daily_return": None,
                "dollar_volume": round(compact_millions * 1_000_000 + 500_000),
                "sector": row.get("sec") or "Unknown",
                "industry": row.get("ind") or "Unknown",
                "market_cap": int(row.get("mc") or 0),
                "universes": row.get("uni") or [],
                "asset_type": row.get("asset_type") or "COMMON_STOCK",
                "observation_grade": "BVT_COMPACT_HISTORY_INTERVAL_MIDPOINT",
            }
            repaired += 1
        payload["movent_normalization"] = {
            "repaired_tickers": repaired,
            "fallback_source": "history.json floored USD millions",
            "fallback_value": "interval midpoint",
        }
        (args.out / f"{trading_date}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        repaired_total += repaired

    print(json.dumps({"sessions": len(history["dates"]), "repaired_observations": repaired_total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
