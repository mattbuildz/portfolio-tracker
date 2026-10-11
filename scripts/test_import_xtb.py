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

print("ok")
