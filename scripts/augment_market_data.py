"""Add current-membership, equal-weight Market MOVENT aggregates to the service artifact."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

MARKETS = {
    "sp-500": ("S&P 500", "S&P 500"),
    "nasdaq-100": ("Nasdaq-100", "Nasdaq 100"),
    "dow-30": ("Dow 30", "Dow Jones"),
    "russell-2000": ("Russell 2000", "Russell 2000"),
}
DOW_30 = {
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
}


def rounded(value):
    return round(value, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=Path("data/movent.json"))
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    memberships = {item["t"]: set(item.get("uni", [])) for item in universe.get("stocks", [])}
    # BVT's latest universe payload currently omits the Dow membership tag.
    # Keep the release's explicit current-membership proxy complete and auditable.
    for ticker in DOW_30:
        memberships.setdefault(ticker, set()).add("Dow Jones")
    markets = []

    for slug, (name, tag) in MARKETS.items():
        members = [stock for stock in payload["stocks"] if tag in memberships.get(stock["ticker"], set())]
        by_date = defaultdict(list)
        for stock in members:
            for point in stock["history"]:
                by_date[point["date"]].append(point["movent"])
        history = [{"date": date, "movent": rounded(statistics.fmean(values))}
                   for date, values in sorted(by_date.items()) if len(values) >= 5]
        latest_index = len(history) - 1

        def change(horizon):
            return rounded(history[latest_index]["movent"] - history[latest_index - horizon]["movent"]) \
                if latest_index >= horizon else None

        markets.append({
            "date": payload["asOf"], "slug": slug, "name": name,
            "movent": history[-1]["movent"], "change1d": change(1), "change5d": change(5),
            "change20d": change(20),
            "activity": rounded(statistics.fmean(stock["activity"] for stock in members)),
            "persistence": rounded(statistics.fmean(stock["persistence"] for stock in members)),
            "breadth": rounded(statistics.fmean(stock["breadth"] for stock in members)),
            "count": sum(stock["relativeActivity"] >= 1.2 for stock in members),
            "total": len(members), "sourceTotal": sum(tag in values for values in memberships.values()),
            "sectorSlugs": sorted({stock["sectorSlug"] for stock in members}),
            "sectors": [{"slug": sector_slug, "name": sector_members[0]["sector"],
                         "movent": rounded(statistics.fmean(x["movent"] for x in sector_members)),
                         "change5d": rounded(statistics.fmean(x["change5d"] for x in sector_members if x["change5d"] is not None)),
                         "activity": rounded(statistics.fmean(x["activity"] for x in sector_members)),
                         "persistence": rounded(statistics.fmean(x["persistence"] for x in sector_members)),
                         "breadth": rounded(statistics.fmean(x["breadth"] for x in sector_members)),
                         "count": sum(x["relativeActivity"] >= 1.2 for x in sector_members), "total": len(sector_members)}
                        for sector_slug in sorted({stock["sectorSlug"] for stock in members})
                        for sector_members in [[stock for stock in members if stock["sectorSlug"] == sector_slug]]],
            "constituents": sorted(stock["ticker"] for stock in members), "history": history[-60:],
        })

    payload["version"] = "movent-data/0.2"
    payload["methodVersion"] = "v0.3"
    payload["universeSnapshotId"] = f"us-equities-{universe.get('asOf', payload['asOf'])}"
    payload["membershipPolicy"] = "current-membership-proxy"
    payload["marketMethod"] = "equal-weight-constituent-movent"
    if args.snapshots:
        payload["sessionCalendar"] = [path.stem for path in sorted(args.snapshots.glob("????-??-??.json"))
                                      if path.stem <= payload["asOf"]]
    payload["releaseStatus"] = "final" if args.final else "update_delayed"
    payload["sessionValidation"] = "pass" if args.final else "legacy_partial"
    payload["markets"] = markets
    payload["counts"]["markets"] = len(markets)
    args.artifact.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({market["slug"]: {key: market[key] for key in ("movent", "change1d", "change5d", "change20d", "total", "sourceTotal")}
                      for market in markets}, indent=2))


if __name__ == "__main__":
    main()
