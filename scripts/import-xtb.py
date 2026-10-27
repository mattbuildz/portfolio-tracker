#!/usr/bin/env python3
"""Turn an XTB account statement (.xlsx) into this app's transactions.

    python3 scripts/import-xtb.py portfolio.xlsx                 # show what it would do
    python3 scripts/import-xtb.py portfolio.xlsx --into "Main Portfolio"

Reads the workbook with the standard library alone — an .xlsx is a zip of XML,
and this needs six columns out of it, not a spreadsheet engine.

Two sheets matter, and the second one is easy to miss. "Open positions" lists
only the shares still held: a lot that was partly sold shows up already
shrunk. The history of the sold part lives in "Closed positions", and each row
there is really *two* transactions — the buy that opened it and the sell that
closed it. Import only the first sheet and the sale vanishes, taking the
realised profit with it.

A third sheet, "Cash operations", holds what the positions do not: dividends,
the tax withheld on them, and the interest on free funds with its own tax. Each
day's amounts for one company (or for the account, in the case of interest)
become one "income" row, net of tax. Deposits, withdrawals and transfers are
the account's plumbing, not a return, and are left out; so are the purchases and
sales it repeats from the position sheets.

Nothing is written unless --into names a portfolio, and the data file is copied
to a .backup first.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import positions as fold_mod                                   # noqa: E402
import store                                                   # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EXCEL_EPOCH = datetime(1899, 12, 30)


# ---------------------------------------------------------------- xlsx reading
def _cells(sheet_xml: bytes, strings: list[str]) -> list[dict[str, str]]:
    """Rows as {column letter: text}, blanks dropped."""
    rows = []
    for row in ET.fromstring(sheet_xml).iter(f"{NS}row"):
        cells: dict[str, str] = {}
        for c in row:
            ref = c.get("r") or ""
            col = "".join(ch for ch in ref if ch.isalpha())
            inline = c.find(f"{NS}is")
            v = c.find(f"{NS}v")
            if inline is not None:
                text = "".join(t.text or "" for t in inline.iter(f"{NS}t"))
            elif v is None:
                continue
            elif c.get("t") == "s":
                text = strings[int(v.text)]
            else:
                text = v.text or ""
            if text != "":
                cells[col] = text
        if cells:
            rows.append(cells)
    return rows


def read_workbook(path: Path) -> dict[str, list[dict[str, str]]]:
    z = zipfile.ZipFile(path)
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            strings.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{NS}sheet")]
    return {name: _cells(z.read(f"xl/worksheets/sheet{i}.xml"), strings)
            for i, name in enumerate(names, 1)}


def _num(text: str | None) -> float:
    try:
        return float(str(text).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def _date(serial: str | None) -> str:
    """XTB writes times as Excel serials; the app stores plain ISO dates."""
    value = _num(serial)
    if value <= 0:
        return ""
    return (EXCEL_EPOCH + timedelta(days=value)).date().isoformat()


def _is_listing(cell: str) -> bool:
    """Whether a cell names an XTB listing such as "NVDA.US" or "1810.HK".

    The Open positions sheet starts with summary lines whose amount column holds
    "5954.65": a dot, but a number, not a listing. Taking it for one wrote a
    company called "5954" into the ledger.
    """
    return re.fullmatch(r"[A-Z0-9\-]+\.[A-Z]{1,4}", cell.strip().upper()) is not None


def _ticker(raw: str) -> str:
    # "NVDA.US" is XTB's name for the listing; the app keys on the symbol.
    return raw.strip().upper().split(".")[0]


# What the cash sheet's "Type" column calls the operations that are income, and
# the ones it repeats from the position sheets or that only move money around.
INCOME_TYPES = {"dividend", "withholding tax", "free funds interest", "free funds interest tax"}
PLUMBING = {"stock purchase", "stock sell", "deposit", "withdrawal", "transfer", "total", "type"}


def income_rows(cash: list[dict[str, str]]) -> tuple[list[dict], list[str]]:
    """One income row per day and company from the cash sheet, net of tax.

    A dividend is paid per lot and its tax is a separate line, so a day can hold
    a dozen of them; summed, they are the one payment the broker made.

    shortcut: a row is identified by its day and company, so a statement that
    adds a late line to a day already imported does not update it.
    """
    net: dict[tuple[str, str], float] = {}
    warnings: list[str] = []
    for row in cash:
        kind = row.get("A", "").strip().lower()
        if not kind or kind in PLUMBING or not row.get("E"):
            continue
        if kind not in INCOME_TYPES:
            warnings.append(f"ignored a cash operation of type {row['A'].strip()!r}")
            continue
        ticker = _ticker(row["C"]) if row.get("C", "").strip() else ""
        key = (_date(row.get("E")), ticker)
        net[key] = net.get(key, 0.0) + _num(row.get("F"))
    out = [{"date": day, "kind": "income", "ticker": ticker, "amount": round(amount, 2),
            "note": f"XTB cash {day} {ticker or 'interest'}"}
           for (day, ticker), amount in sorted(net.items()) if abs(amount) >= 0.005]
    return out, sorted(set(warnings))


# ------------------------------------------------------------------ extraction
def _sheet(book: dict[str, list[dict[str, str]]], column: str, title: str) -> list[dict[str, str]]:
    # Found by its header row: XTB has shipped the sheets in more than one order.
    return next((rows for rows in book.values() if any(r.get(column) == title for r in rows)), [])


def extract(book: dict[str, list[dict[str, str]]]) -> tuple[list[dict], dict[str, str], list[str], dict]:
    """Every buy, sell and income payment in the statement, plus company names for display."""
    transactions: list[dict] = []
    names: dict[str, str] = {}
    warnings: list[str] = []
    # XTB's own totals, kept so the import can be checked against them rather
    # than merely trusted.
    broker: dict[str, dict] = {}

    # --- sheet one: open positions ------------------------------------------
    # Two kinds of row share this table. A row with a Type (column E) is one
    # real trade. A row without one, but with a Category, is XTB's own subtotal
    # for the company — not a transaction, but exactly the number to check
    # against, so it is kept aside instead of skipped.
    for row in _sheet(book, "B", "Instrument/Position"):
        ticker_cell = row.get("C", "")
        if not _is_listing(ticker_cell):
            continue
        ticker = _ticker(ticker_cell)
        kind = row.get("E", "").strip().upper()
        if not kind:
            if row.get("D", "").strip():
                names[ticker] = row.get("B", ticker).strip()
                broker[ticker] = {"shares": _num(row.get("F")), "avg_price": _num(row.get("I"))}
            continue
        if kind not in ("BUY", "SELL"):
            warnings.append(f"{ticker}: ignored an open position of type {kind!r}")
            continue
        if kind == "SELL":
            warnings.append(f"{ticker}: ignored an open short position — this app only models long holdings")
            continue
        transactions.append({
            "date": _date(row.get("J")), "kind": "buy", "ticker": ticker,
            "shares": _num(row.get("F")), "price": _num(row.get("I")),
            "fee": _num(row.get("Q")), "note": f"XTB #{row.get('B', '').strip()}",
        })

    # --- sheet two: closed positions ----------------------------------------
    # One row, two transactions: it opened and it closed.
    for row in _sheet(book, "X", "Position ID"):
        ticker_cell = row.get("B", "")
        if not _is_listing(ticker_cell):
            continue
        kind = row.get("D", "").strip().upper()
        if not kind:
            # The sheet opens with a couple of header lines, and one of them
            # holds a date serial in the ticker column — numeric enough to look
            # like a row. A position always names its type; these never do.
            continue
        ticker = _ticker(ticker_cell)
        if kind != "BUY":
            warnings.append(f"{ticker}: ignored a closed position of type {kind!r}"
                            " — this app only models long holdings")
            continue
        qty, ident = _num(row.get("E")), row.get("X", "").strip()
        transactions.append({
            "date": _date(row.get("G")), "kind": "buy", "ticker": ticker,
            "shares": qty, "price": _num(row.get("F")),
            "fee": _num(row.get("Q")), "note": f"XTB #{ident}",
        })
        transactions.append({
            "date": _date(row.get("I")), "kind": "sell", "ticker": ticker,
            "shares": qty, "price": _num(row.get("H")),
            "fee": 0.0, "note": f"XTB #{ident} closed",
            "broker_realised": _num(row.get("K")),
        })

    income, income_warnings = income_rows(_sheet(book, "E", "Time"))
    return transactions + income, names, warnings + income_warnings, broker


# -------------------------------------------------------------------- checking
def report(transactions: list[dict], names: dict, broker: dict) -> bool:
    """Fold the imported transactions and hold the result against XTB's totals."""
    computed, realised = fold_mod.fold(transactions)
    broker_realised = sum(t.get("broker_realised", 0.0) for t in transactions)

    print(f"\n{len(transactions)} transactions "
          f"({sum(1 for t in transactions if t['kind'] == 'buy')} buys, "
          f"{sum(1 for t in transactions if t['kind'] == 'sell')} sells, "
          f"{sum(1 for t in transactions if t['kind'] == 'income')} income, "
          f"{sum(t['amount'] for t in transactions if t['kind'] == 'income'):,.2f} USD net)\n")
    print(f"{'':6}{'shares':>12}{'XTB':>12}{'avg price':>12}{'XTB':>10}{'':>4}")

    agreed = True
    for ticker in sorted(computed):
        pos, ref = computed[ticker], broker.get(ticker)
        if not ref:
            print(f"{ticker:6}{pos['shares']:12.4f}{'—':>12}{pos['avg_price']:12.4f}{'—':>10}")
            continue
        # XTB rounds its own summary to the cent, so agreement means agreement
        # to the precision it prints, not to the last float bit.
        ok = abs(pos["shares"] - ref["shares"]) < 5e-5 and abs(pos["avg_price"] - ref["avg_price"]) < 5e-3
        agreed = agreed and ok
        print(f"{ticker:6}{pos['shares']:12.4f}{ref['shares']:12.4f}"
              f"{pos['avg_price']:12.4f}{ref['avg_price']:10.2f}{'  ok' if ok else '  ✗':>4}")

    print(f"\nrealised profit: {realised:,.2f} USD   XTB: {broker_realised:,.2f} USD"
          f"{'   ok' if abs(realised - broker_realised) < 0.02 else '   ✗'}")
    return agreed


def _key(t: dict):
    # A position is bought once but can be closed in parts, each on its own
    # day, so the id alone would swallow every sale after the first.
    if t.get("kind") == "sell":
        return t.get("note"), t.get("date"), round(t.get("shares") or 0, 4)
    return t.get("note")


def merge(existing: list[dict], transactions: list[dict]) -> tuple[list[dict], list[str]]:
    """What a statement adds to a ledger: the rows to append, and a line for
    each hand-typed row it recognised and brought into line with XTB."""
    # Re-importing the same statement must not double the portfolio: XTB's own
    # position ids come along in the note, and they are what makes a row unique.
    seen = {_key(t) for t in existing if t.get("note")}
    typed = [t for t in existing if not t.get("note")]
    fresh, adopted = [], []
    for t in transactions:
        if _key(t) in seen:
            continue
        if t["kind"] == "income":
            fresh.append(t)         # its note is its identity, and nobody types these in
            continue
        # A trade typed in before its statement arrived has no id to match on.
        # Same company, same side, same volume is that trade: the statement's
        # date and price replace the typed ones rather than landing beside them.
        same = next((e for e in typed if (e.get("kind"), e.get("ticker"), round(e.get("shares") or 0, 4))
                     == (t["kind"], t["ticker"], round(t["shares"], 4))), None)
        if same is None:
            fresh.append(t)
            continue
        typed = [e for e in typed if e is not same]
        adopted.append(f"{t['ticker']} {t['kind']} {t['shares']:g}: hand-typed row is {t['note']}, "
                       f"price {same.get('price')} → {t['price']}")
        same.update(date=t["date"], price=t["price"], fee=t["fee"], note=t["note"])
    return fresh, adopted


def disagreements(ledger: list[dict], broker: dict) -> list[str]:
    """Companies whose share count in the finished ledger is not what XTB holds.

    report() checks the statement against itself. This checks the result, which
    is what catches a trade that is in the ledger twice or a sale that fell in
    a month whose statement was never imported."""
    held, _ = fold_mod.fold(ledger)
    ours = {t["ticker"] for t in ledger if str(t.get("note") or "").startswith("XTB #")} | set(broker)
    off = []
    for ticker in sorted(ours):
        here = held.get(ticker, {}).get("shares", 0.0)
        there = broker.get(ticker, {}).get("shares", 0.0)
        if abs(here - there) >= 5e-5:
            off.append(f"{ticker}: {here:.4f} shares after import, {there:.4f} at XTB")
    return off


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("statement", type=Path)
    ap.add_argument("--into", metavar="PORTFOLIO",
                    help="name of the portfolio to write into; without this nothing is written")
    ap.add_argument("--replace", action="store_true",
                    help="discard that portfolio's existing transactions instead of appending")
    args = ap.parse_args()

    if not args.statement.exists():
        print(f"no such file: {args.statement}", file=sys.stderr)
        return 1

    try:
        book = read_workbook(args.statement)
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        print("this file is not an .xlsx statement", file=sys.stderr)
        return 1
    transactions, names, warnings, broker = extract(book)
    if not transactions:
        print("no transactions found — is this an XTB statement?", file=sys.stderr)
        return 1

    for w in warnings:
        print(f"  note: {w}")
    agreed = report(transactions, names, broker)
    for t in transactions:
        t.pop("broker_realised", None)

    if not args.into:
        print("\nNothing written. Re-run with --into \"<portfolio name>\" to import.")
        return 0
    if not agreed:
        print("\nRefusing to import: the fold does not agree with XTB's own totals.", file=sys.stderr)
        return 1

    state = store.load()
    # The app passes the id, since two portfolios may share a name.
    target = next((pf for pf in state["portfolios"] if pf["id"] == args.into
                   or pf["name"].strip().lower() == args.into.strip().lower()), None)
    if target is None:
        have = ", ".join(pf["name"] for pf in state["portfolios"])
        print(f"\nno portfolio named {args.into!r} — have: {have}", file=sys.stderr)
        return 1

    existing = [] if args.replace else list(target.get("transactions") or [])
    fresh, adopted = merge(existing, transactions)
    off = disagreements(existing + fresh, broker)
    if off:
        print("\nNothing imported: the portfolio would not match XTB afterwards.\n  "
              + "\n  ".join(off)
              + "\nToo many shares: a trade is recorded twice — strike the extra row in Transactions."
                "\nToo few or a sale missing: import the statement that covers the missing period first.",
              file=sys.stderr)
        return 1

    data_file = store.path()
    if data_file.exists():
        backup = data_file.with_suffix(".backup.json")
        shutil.copy2(data_file, backup)
        print(f"\nbacked up {data_file} → {backup}")
    for line in adopted:
        print(f"matched {line}")
    target["transactions"] = existing + [store.new_transaction(t) for t in fresh]
    store.retire_targets(target, {t["ticker"] for t in fresh if t["kind"] == "sell"})

    meta = target.setdefault("meta", {})
    for ticker, name in names.items():
        meta.setdefault(ticker, {}).setdefault("name", name)

    store.save(state)
    skipped = len(transactions) - len(fresh)
    print(f"imported {len(fresh)} transactions into {target['name']!r}"
          + (f" ({skipped} already there, skipped)" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
