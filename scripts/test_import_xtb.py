"""Checks for what import-xtb.py adds to a ledger. Run: python3 scripts/test_import_xtb.py"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("import_xtb", Path(__file__).with_name("import-xtb.py"))
xtb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xtb)


def tx(kind, shares, price, note, date="2025-03-03", ticker="AAA"):
    return {"date": date, "kind": kind, "ticker": ticker, "shares": shares, "price": price, "fee": 0.0, "note": note}


# A lot of 5 shares, 2 of them sold in June: the ledger after the first statement.
ledger = [tx("buy", 3, 100.0, "XTB #1"), tx("buy", 2, 100.0, "XTB #1"),
          tx("sell", 2, 150.0, "XTB #1 closed", "2025-06-10")]
# The next statement closes the rest of the same position.
statement = [tx("buy", 3, 100.0, "XTB #1"), tx("sell", 3, 180.0, "XTB #1 closed", "2025-09-15")]

fresh, adopted = xtb.merge(ledger, statement)
assert [t["kind"] for t in fresh] == ["sell"] and not adopted, "the second sale of a position must be kept"
assert xtb.merge(ledger + fresh, statement) == ([], []), "the same statement twice must add nothing"
assert xtb.disagreements(ledger + fresh, {}) == []
assert xtb.disagreements(ledger, {}), "shares XTB no longer holds must be reported"

# A buy typed by hand before its statement arrived becomes that statement's row.
typed = [tx("buy", 1.5, 50.0, "", "2025-08-01", "BBB")]
fresh, adopted = xtb.merge(typed, [tx("buy", 1.5, 52.0, "XTB #2", "2025-08-01", "BBB")])
assert not fresh and len(adopted) == 1 and typed[0]["price"] == 52.0 and typed[0]["note"] == "XTB #2"
assert xtb.disagreements(typed, {"BBB": {"shares": 1.5}}) == []

# A summary line's amount is not a listing; real ones are, digits first or not.
assert [xtb._is_listing(c) for c in ("NVDA.US", "BRK-B.US", "1810.HK", "5954.65", "", "Ticker", "406")] \
    == [True, True, True, False, False, False, False]

# The cash sheet: a dividend is paid per lot and taxed on a line of its own; one
# day and company is one income row, net. Deposits and the rest are not income.
def cash(kind, day, amount, ticker=""):
    return {"A": kind, "C": ticker, "E": str(day), "F": str(amount)}

rows = [cash("Dividend", 46000.5, 0.80, "MSFT.US"), cash("Withholding tax", 46000.6, -0.12, "MSFT.US"),
        cash("Dividend", 46000.7, 0.15, "MSFT.US"), cash("Withholding tax", 46000.8, -0.02, "MSFT.US"),
        cash("Free funds interest", 46010.1, 0.06), cash("Free funds interest tax", 46010.2, -0.06),
        cash("Deposit", 46000.9, 500), cash("Transfer", 46001.1, 100), cash("Stock purchase", 46001.2, -50),
        {"A": "Type", "E": "Time", "F": "Amount"}, {"A": "Total", "F": "99"}, cash("Custody fee", 46002.1, -1)]
income, warnings = xtb.income_rows(rows)
assert [(t["ticker"], t["amount"]) for t in income] == [("MSFT", 0.81)], income    # interest nets to zero: dropped
assert warnings == ["ignored a cash operation of type 'Custody fee'"], warnings
again, _ = xtb.merge(ledger + income, income)
assert again == [], "the same income twice must add nothing"
assert xtb.merge(ledger, income)[0] == income

print("ok")
