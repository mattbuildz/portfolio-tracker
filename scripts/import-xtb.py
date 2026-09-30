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

Nothing is written unless --into names a portfolio, and the data file is copied
to a .backup first.
"""

from __future__ import annotations

import argparse
import json
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


def _ticker(raw: str) -> str:
    # "NVDA.US" is XTB's name for the listing; the app keys on the symbol.
    return raw.strip().upper().split(".")[0]


# ------------------------------------------------------------------ extraction
def extract(book: dict[str, list[dict[str, str]]]) -> tuple[list[dict], dict[str, str], list[str], dict]:
    """Every buy and sell in the statement, plus company names for display."""
    transactions: list[dict] = []
    names: dict[str, str] = {}
    warnings: list[str] = []
    # XTB's own totals, kept so the import can be checked against them rather
    # than merely trusted.
    broker: dict[str, dict] = {}

    sheets = list(book.values())

    # --- sheet one: open positions ------------------------------------------
    # Two kinds of row share this table. A row with a Type (column E) is one
    # real trade. A row without one, but with a Category, is XTB's own subtotal
    # for the company — not a transaction, but exactly the number to check
    # against, so it is kept aside instead of skipped.
    for row in sheets[0]:
        ticker_cell = row.get("C", "")
        if not ticker_cell or "." not in ticker_cell:
            continue
        ticker = _ticker(ticker_cell)
        kind = row.get("E", "").strip().upper()
        if not kind:
            if row.get("D", "").strip().upper() == "STOCK":
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
    for row in sheets[1] if len(sheets) > 1 else []:
        ticker_cell = row.get("B", "")
        if not ticker_cell or "." not in ticker_cell or ticker_cell.strip().lower() == "ticker":
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

    return transactions, names, warnings, broker


# -------------------------------------------------------------------- checking
def report(transactions: list[dict], names: dict, broker: dict) -> bool:
    """Fold the imported transactions and hold the result against XTB's totals."""
    computed, realised = fold_mod.fold(transactions)
    broker_realised = sum(t.get("broker_realised", 0.0) for t in transactions)

    print(f"\n{len(transactions)} transactions "
          f"({sum(1 for t in transactions if t['kind'] == 'buy')} buys, "
          f"{sum(1 for t in transactions if t['kind'] == 'sell')} sells)\n")
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

    transactions, names, warnings, broker = extract(read_workbook(args.statement))
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
    target = next((pf for pf in state["portfolios"]
                   if pf["name"].strip().lower() == args.into.strip().lower()), None)
    if target is None:
        have = ", ".join(pf["name"] for pf in state["portfolios"])
        print(f"\nno portfolio named {args.into!r} — have: {have}", file=sys.stderr)
        return 1

    data_file = store.path()
    if data_file.exists():
        backup = data_file.with_suffix(".backup.json")
        shutil.copy2(data_file, backup)
        print(f"\nbacked up {data_file} → {backup}")

    existing = [] if args.replace else list(target.get("transactions") or [])
    # Re-importing the same statement must not double the portfolio: XTB's own
    # position ids come along in the note, and they are what makes a row unique.
    seen = {t.get("note") for t in existing if t.get("note")}
    fresh = [t for t in transactions if t.get("note") not in seen]
    target["transactions"] = existing + [store.new_transaction(t) for t in fresh]

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
