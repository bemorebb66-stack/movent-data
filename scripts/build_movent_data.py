"""Build MOVENT service data from immutable BVT replay snapshots.

The scoring functions are frozen from MOVENT_INDEX_METHODOLOGY_V0.1.md and
validate_movent_index.py. Price direction never enters a MOVENT score.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

WEIGHTS = (0.40, 0.35, 0.25)
MIN_BASELINE_DV = 1_000_000
ACTIVE_RATIO = 1.20
REGRESSIONS = {
    ("2026-07-22", "SMCI"): (100.0, 44.96930692522399, 23.076923076923077, 61.50848819305916),
    ("2026-08-04", "PLTR"): (100.0, 59.552590425027894, 65.45454545454545, 77.20704301239613),
    ("2026-06-26", "AUPH"): (99.49137198291733, 89.0614003668521, 97.61904761904762, 95.37280082632708),
}


def ratio_score(ratio: float) -> float:
    if not ratio or ratio <= 0:
        return 0.0
    return max(0.0, min(100.0, 50.0 + 25.0 * math.log2(ratio)))


def slugify(value: str) -> str:
    chars = []
    for char in value.lower().replace("&", " and "):
        chars.append(char if char.isalnum() else "-")
    return "-".join(part for part in "".join(chars).split("-") if part) or "unknown"


def load_snapshots(root: Path):
    days = []
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        stocks = {}
        for ticker, item in data.get("tickers", {}).items():
            dv = item.get("dollar_volume")
            if not isinstance(dv, (int, float)) or dv <= 0:
                continue
            asset = item.get("asset_type")
            if asset and asset != "COMMON_STOCK":
                continue
            stocks[ticker] = {
                "dv": float(dv), "ret": item.get("daily_return"),
                "name": item.get("name") or ticker, "price": item.get("close_price"),
                "sector": item.get("sector") or "Unknown",
                "industry": item.get("industry") or "Unknown",
            }
        days.append((data.get("trading_date") or path.stem, stocks))
    return days


def calculate(days):
    histories, activity_history = defaultdict(list), defaultdict(list)
    rows = []
    for date, stocks in days:
        today = {}
        for ticker, item in stocks.items():
            prior = histories[ticker][-20:]
            if len(prior) < 20:
                continue
            median20, mean20 = statistics.median(prior), statistics.fmean(prior)
            if median20 < MIN_BASELINE_DV:
                continue
            ratio = item["dv"] / median20
            today[ticker] = {**item, "ratio": ratio, "activity": ratio_score(ratio), "rvol": item["dv"] / mean20}
        industry_members, sector_members = defaultdict(list), defaultdict(list)
        for ticker, item in today.items():
            industry_members[item["industry"]].append(ticker)
            sector_members[item["sector"]].append(ticker)
        for ticker, item in today.items():
            peers, source = industry_members[item["industry"]], "industry"
            if len(peers) < 5:
                peers, source = sector_members[item["sector"]], "sector_fallback"
            if len(peers) < 5:
                continue
            active = sum(today[peer]["ratio"] >= ACTIVE_RATIO for peer in peers)
            breadth = 100.0 * (active + 1) / (len(peers) + 2)
            recent = activity_history[ticker][-4:] + [item["activity"]]
            if len(recent) < 4:
                continue
            persistence = statistics.fmean(recent)
            movent = WEIGHTS[0] * item["activity"] + WEIGHTS[1] * persistence + WEIGHTS[2] * breadth
            rows.append({"date": date, "ticker": ticker, **item, "persistence": persistence,
                         "breadth": breadth, "breadth_n": len(peers), "breadth_source": source,
                         "active_n": active, "movent": movent})
        for ticker, item in stocks.items():
            histories[ticker].append(item["dv"])
            if ticker in today:
                activity_history[ticker].append(today[ticker]["activity"])
    return rows


def calculate_groups(days, stock_rows, level):
    constituent = defaultdict(list)
    for row in stock_rows:
        constituent[(row["date"], row[level])].append(row)
    history, activity_history, output = defaultdict(list), defaultdict(list), []
    for date, stocks in days:
        totals = defaultdict(float)
        for item in stocks.values():
            totals[item[level]] += item["dv"]
        for group, dv in totals.items():
            prior = history[group][-20:]
            if len(prior) < 20:
                continue
            ratio = dv / statistics.median(prior)
            activity = ratio_score(ratio)
            recent = activity_history[group][-4:] + [activity]
            if len(recent) < 4:
                continue
            persistence = statistics.fmean(recent)
            members = constituent.get((date, group), [])
            if len(members) < 5:
                continue
            active = sum(row["ratio"] >= ACTIVE_RATIO for row in members)
            breadth = 100.0 * (active + 1) / (len(members) + 2)
            movent = WEIGHTS[0] * activity + WEIGHTS[1] * persistence + WEIGHTS[2] * breadth
            output.append({"date": date, "level": level, "name": group, "dv": dv, "ratio": ratio,
                           "activity": activity, "persistence": persistence, "breadth": breadth,
                           "member_n": len(members), "active_n": active, "movent": movent})
        for group, dv in totals.items():
            history[group].append(dv)
            if len(history[group]) > 20:
                activity_history[group].append(ratio_score(dv / statistics.median(history[group][-21:-1])))
    return output


def deltas(rows, id_key):
    dates = sorted({row["date"] for row in rows})
    date_index = {date: index for index, date in enumerate(dates)}
    lookup = {(row[id_key], date_index[row["date"]]): row for row in rows}
    for row in rows:
        index = date_index[row["date"]]
        for horizon in (1, 5, 20):
            previous = lookup.get((row[id_key], index - horizon))
            row[f"change{horizon}d"] = row["movent"] - previous["movent"] if previous else None


def rounded(value, digits=1):
    return None if value is None else round(value, digits)


def assert_regressions(rows):
    lookup = {(row["date"], row["ticker"]): row for row in rows}
    output = []
    for key, expected in REGRESSIONS.items():
        row = lookup[key]
        actual = (row["activity"], row["persistence"], row["breadth"], row["movent"])
        if any(abs(a - e) > 1e-9 for a, e in zip(actual, expected)):
            raise AssertionError(f"MOVENT regression mismatch for {key}: {actual} != {expected}")
        output.append({"date": key[0], "ticker": key[1], "activity": actual[0],
                       "persistence": actual[1], "breadth": actual[2], "movent": actual[3]})
    return output


def build(days, regression_days=None):
    rows = calculate(days)
    regression = assert_regressions(calculate(regression_days)) if regression_days is not None else assert_regressions(rows)
    # A published stock must have an unbroken current session plus 20-session
    # lookback so every 1D/5D/20D comparison is real rather than inferred.
    required_dates = [date for date, stocks in days if stocks][-21:]
    dates_by_ticker = defaultdict(set)
    for row in rows:
        dates_by_ticker[row["ticker"]].add(row["date"])
    eligible_tickers = {ticker for ticker, dates in dates_by_ticker.items()
                        if all(date in dates for date in required_dates)}
    rows = [row for row in rows if row["ticker"] in eligible_tickers]
    deltas(rows, "ticker")
    industry_rows = calculate_groups(days, rows, "industry")
    sector_rows = calculate_groups(days, rows, "sector")
    deltas(industry_rows, "name"); deltas(sector_rows, "name")
    latest_date = max(row["date"] for row in rows)
    by_ticker = defaultdict(list)
    for row in rows:
        by_ticker[row["ticker"]].append(row)
    latest = []
    for ticker, history in by_ticker.items():
        row = history[-1]
        if row["date"] != latest_date:
            continue
        recent = history[-60:]
        latest.append({
            "date": row["date"], "ticker": ticker, "company": row["name"],
            "sector": row["sector"], "sectorSlug": slugify(row["sector"]),
            "industry": row["industry"], "industrySlug": slugify(row["industry"]),
            "price": rounded(row["price"], 2), "priceChange": rounded(row["ret"], 2),
            "dollarVolume": round(row["dv"]), "relativeActivity": rounded(row["ratio"], 4),
            "activity": rounded(row["activity"]), "persistence": rounded(row["persistence"]),
            "breadth": rounded(row["breadth"]), "breadthCount": row["active_n"],
            "breadthTotal": row["breadth_n"], "breadthSource": row["breadth_source"],
            "movent": rounded(row["movent"]), "change1d": rounded(row["change1d"]),
            "change5d": rounded(row["change5d"]), "change20d": rounded(row["change20d"]),
            "days5": sum(item["ratio"] >= ACTIVE_RATIO for item in history[-5:]),
            "days20": sum(item["ratio"] >= ACTIVE_RATIO for item in history[-20:]),
            "history": [{"date": item["date"], "movent": rounded(item["movent"]),
                         "price": rounded(item["price"], 2)} for item in recent],
        })
    def latest_groups(source):
        return [{"date": row["date"], "slug": slugify(row["name"]), "name": row["name"],
                 "movent": rounded(row["movent"]), "change1d": rounded(row["change1d"]),
                 "change5d": rounded(row["change5d"]), "change20d": rounded(row["change20d"]),
                 "activity": rounded(row["activity"]), "persistence": rounded(row["persistence"]),
                 "breadth": rounded(row["breadth"]), "count": row["active_n"], "total": row["member_n"],
                 "dollarVolume": round(row["dv"])} for row in source if row["date"] == latest_date]
    industries = latest_groups(industry_rows); sectors = latest_groups(sector_rows)
    sector_by_industry = {}
    for row in latest:
        sector_by_industry.setdefault(row["industrySlug"], row["sector"])
    for item in industries:
        item["sector"] = sector_by_industry.get(item["slug"], "Unknown")
    industry_slugs = {item["slug"] for item in industries}
    sector_slugs = {item["slug"] for item in sectors}
    industry_snapshot = {item["slug"]: item for item in industries}
    sector_snapshot = {item["slug"]: item for item in sectors}
    for stock in latest:
        stock["industryAvailable"] = stock["industrySlug"] in industry_slugs
        stock["industryRoute"] = f'/industries/{stock["industrySlug"]}' if stock["industryAvailable"] else None
        stock["sectorRoute"] = f'/sectors/{stock["sectorSlug"]}' if stock["sectorSlug"] in sector_slugs else None
        peer_snapshot = industry_snapshot.get(stock["industrySlug"]) if stock["breadthSource"] == "industry" \
            else sector_snapshot.get(stock["sectorSlug"])
        if peer_snapshot:
            # Public peer counts must use the same release snapshot as the
            # corresponding Industry/Sector page, never a historical row set.
            stock["breadthCount"] = peer_snapshot["count"]
            stock["breadthTotal"] = peer_snapshot["total"]
    return {"version": "movent-data/0.1", "asOf": latest_date,
            "methodVersion": "v0.3", "universeSnapshotId": f"us-equities-{latest_date}",
            "eligibilityPolicy": "continuous-scored-sessions-21",
            "counts": {"stocks": len(latest), "industries": len(industries), "sectors": len(sectors)},
            "regressions": regression, "stocks": sorted(latest, key=lambda row: row["ticker"]),
            "industries": sorted(industries, key=lambda row: row["name"]),
            "sectors": sorted(sectors, key=lambda row: row["name"])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--regression-snapshots", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/movent.json"))
    args = parser.parse_args()
    regression_days = load_snapshots(args.regression_snapshots) if args.regression_snapshots else None
    payload = build(load_snapshots(args.snapshots), regression_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"asOf": payload["asOf"], **payload["counts"], "regressions": payload["regressions"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
