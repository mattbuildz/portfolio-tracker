"""Checks for app/history.py. Run: python3 scripts/test_history.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import history


def tx(kind, shares, price, day, ticker="AAA", **extra):
    return {"date": day, "kind": kind, "ticker": ticker, "shares": shares, "price": price, "fee": 0, **extra}


days = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
flat = {"AAA": dict(zip(days, [10.0, 11.0, 11.0, 12.1]))}
bench = {"IDX": dict(zip(days, [100.0, 110.0, 110.0, 121.0]))}

# Held without trading, the portfolio moves exactly like the index.
ledger = [tx("buy", 10, 10.0, "2026-01-05"), tx("buy", 50, 11.0, "2026-01-06")]
r = history.series(ledger[:1], flat, bench)
assert r["dates"] == days
assert [round(x, 3) for x in r["portfolio"]] == [0.0, 10.0, 10.0, 21.0], r["portfolio"]
# Money paid in on day two counts from the start of that day: 10 of profit over
# 100 held plus 550 paid in. The buy itself is not a gain.
r = history.series(ledger, flat, bench)
assert [round(x, 3) for x in r["portfolio"]] == [0.0, 1.538, 1.538, 11.692], r["portfolio"]
assert r["benchmarks"]["IDX"] == [0.0, 10.0, 10.0, 21.0]

# Value is what is held at the close; invested is net money paid in, so a sale
# lowers it and value - invested is the overall profit.
assert r["value"] == [100.0, 660.0, 660.0, 726.0] and r["invested"] == [100.0, 650.0, 650.0, 650.0], r
sold = history.series(ledger + [tx("sell", 10, 12.1, "2026-01-08")], flat, {})
assert sold["invested"][-1] == 650.0 - 121.0
assert abs((sold["value"][-1] - sold["invested"][-1]) - (60 * 12.1 - 650.0)) < 1e-6, sold

# A trade on a weekend lands on the next trading day; the index carries over it.
r = history.series([tx("buy", 10, 10.0, "2026-01-03")], flat, bench)
assert r["dates"][0] == days[0] and r["portfolio"][-1] == 21.0, r

# A split after the buy: bars are split-adjusted (price already halved back in
# time), so the 2:1 split must not double the history.
adj = {"AAA": dict(zip(days, [5.0, 5.5, 5.5, 6.05]))}
r = history.series([tx("buy", 10, 10.0, "2026-01-05"), tx("split", 0, 0, "2026-01-07", ratio=2)], adj, {})
assert [round(x, 3) for x in r["portfolio"]] == [0.0, 10.0, 10.0, 21.0], r["portfolio"]

# A ticker with no bars is reported and does not count as a loss.
r = history.series([tx("buy", 10, 10.0, "2026-01-05"), tx("buy", 5, 7.0, "2026-01-05", ticker="ZZZ")], flat, {})
assert r["left_out"] == ["ZZZ"] and r["portfolio"][-1] == 21.0

# Bars that reach back past the first purchase: the first day counts too — the
# portfolio from price paid to close, the index from the close before.
early = {"AAA": {"2026-01-02": 9.0, **flat["AAA"]}}
r = history.series([tx("buy", 10, 9.5, "2026-01-05")], early, {"IDX": {"2026-01-02": 90.0, **bench["IDX"]}})
assert abs(r["portfolio"][0] - 5.263) < 0.01 and abs(r["benchmarks"]["IDX"][0] - 11.111) < 0.01, r

# Income lowers the money paid in (so it is profit there) but is not part of the
# price return; interest has no ticker.
inc = {"date": "2026-01-06", "kind": "income", "ticker": "", "amount": 5.0, "note": ""}
r = history.series([tx("buy", 10, 10.0, "2026-01-05"), inc], flat, {})
assert [round(x, 3) for x in r["portfolio"]] == [0.0, 10.0, 10.0, 21.0], r["portfolio"]
assert r["invested"] == [100.0, 95.0, 95.0, 95.0] and r["left_out"] == [], r

# An empty ledger draws nothing.
assert history.series([], {}, {})["dates"] == []
print("ok")
