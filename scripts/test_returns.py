"""Checks for app/returns.py. Run: python3 scripts/test_returns.py"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import positions
import returns


def tx(kind, shares, price, day, ticker="AAA", fee=0.0):
    return {"date": day, "kind": kind, "ticker": ticker, "shares": shares, "price": price, "fee": fee}


today = date(2027, 1, 1)

# One buy a year ago, +10%: profit 10, return 10%, and CAGR must equal it.
r = returns.compute([tx("buy", 10, 10.0, "2026-01-01")], 110.0, today)
assert abs(r["profit"] - 10) < 1e-9 and abs(r["return_pct"] - 10) < 1e-9
assert abs(r["cagr_pct"] - 10) < 0.01, r

# Overall profit is exactly unrealised + realised.
ledger = [tx("buy", 10, 10.0, "2026-01-01"), tx("sell", 4, 15.0, "2026-06-01", fee=1.0)]
pos, realised = positions.fold(ledger)
value = pos["AAA"]["shares"] * 12.0
unrealised = value - pos["AAA"]["cost"]
r = returns.compute(ledger, value, today)
assert abs(r["profit"] - (unrealised + realised)) < 1e-9, (r, unrealised, realised)
assert abs(r["invested"] - 100) < 1e-9

# Income (dividends, interest, net of tax) is profit on top of the two parts, and
# counts as money received on its own day. Interest has no ticker.
earned = [tx("buy", 10, 10.0, "2026-01-01"), {"date": "2026-07-01", "kind": "income", "ticker": "", "amount": 5.0},
          {"date": "2026-07-01", "kind": "income", "ticker": "AAA", "amount": 2.5}]
r = returns.compute(earned, 110.0, today)
assert abs(r["profit"] - 17.5) < 1e-9 and abs(r["income"] - 7.5) < 1e-9 and abs(r["return_pct"] - 17.5) < 1e-9, r
assert r["cagr_pct"] > returns.compute([tx("buy", 10, 10.0, "2026-01-01")], 110.0, today)["cagr_pct"]

# Late money has had less time: same 20 profit, but the second lot arrived late,
# so the yearly rate is higher than profit / invested.
late = [tx("buy", 10, 10.0, "2026-01-01"), tx("buy", 10, 10.0, "2026-10-01")]
r = returns.compute(late, 220.0, today)
assert abs(r["return_pct"] - 10) < 1e-9 and r["cagr_pct"] > 10, r

# Too short to annualise.
r = returns.compute([tx("buy", 1, 10.0, "2026-12-20")], 11.0, today)
assert r["cagr_pct"] is None and "too short" in r["note"]

# Windows: longer than the history is the same figure, flagged; inside it is unavailable.
one = [tx("buy", 10, 10.0, "2026-01-01")]
assert returns.compute(one, 110.0, today, period="3y")["clamped"]
assert returns.compute(one, 110.0, today, period="3y")["cagr_pct"] == returns.compute(one, 110.0, today)["cagr_pct"]
old = [tx("buy", 10, 10.0, "2020-01-01")]
r = returns.compute(old, 110.0, today, period="1y")
assert r["profit"] is None and "price history" in r["note"]

# Nothing bought: no division by zero.
r = returns.compute([], 0.0, today)
assert r["return_pct"] is None and r["cagr_pct"] is None
# Simple CAGR: a year of +10% is 10%; the same +10% in half a year is 21%.
assert abs(returns.compute([tx("buy", 10, 10.0, "2026-01-01")], 110.0, today)["cagr_simple_pct"] - 10) < 0.01
half = returns.compute([tx("buy", 10, 10.0, "2026-07-02")], 110.0, today)
assert abs(half["cagr_simple_pct"] - 21) < 0.5, half
print("ok")
