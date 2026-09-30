#!/usr/bin/env python3
"""Export the portfolio snapshot by hand.

The app writes this file by itself after every price fetch, so this script is
for the times the app is not running, or when the file should land somewhere
other than the vault. The writing itself lives in app/snapshot.py — the same
code the server calls, so a hand-made export and an automatic one cannot drift.

    python3 scripts/portfolio-snapshot.py              # default destination
    python3 scripts/portfolio-snapshot.py --stdout     # print, write nothing
    python3 scripts/portfolio-snapshot.py -o file.md   # somewhere else
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app"))

import snapshot                             # noqa: E402
import store                                # noqa: E402

APP_URL = "http://127.0.0.1:8777/api/state"


def prices_from_app() -> tuple[dict, str] | None:
    """Quotes the running app already has, or None if it is not running.

    Preferred over fetching again: these are the prices on the user's screen,
    and a second fetch would produce a file that quietly disagrees with it.
    """
    try:
        with urllib.request.urlopen(APP_URL, timeout=2) as response:
            state = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None
    quotes = {
        row["ticker"]: {"price": row.get("price"), "source": row.get("source"),
                        "as_of": row.get("as_of"),
                        # Carried, not dropped: the renderer tells a bond's
                        # computed stamp from a venue's by this field, and
                        # without it a bond looks like a quote lagging behind.
                        "as_of_kind": row.get("as_of_kind")}
        for row in state.get("holdings", []) if row.get("price")
    }
    return (quotes, "running app") if quotes else None


def prices_direct(tickers: list[str]) -> tuple[dict, str]:
    """Fetch quotes ourselves, when the app is not running.

    The same split the server makes, and for the same reason: a savings bond
    has no venue to ask, so its price comes from its own terms in `meta`. This
    used to ask only the quote venues, which left a held bond unpriced, valued
    at zero and every other weight in the file inflated — the snapshot said so,
    but a snapshot that has to apologise for a whole position is the wrong
    output when the arithmetic was available all along.
    """
    import bonds
    import prices as price_source

    specs = store.bond_specs(store.load())
    computed = {t: specs[t] for t in tickers if t in specs}
    listed = [t for t in tickers if t not in specs]

    out: dict[str, dict] = {}
    unpriced: list[str] = []
    notes: list[str] = []

    if computed:
        try:
            fx = price_source.fetch_fx()
            got, refused = bonds.quotes(computed, fx)
            out.update({t: q.as_dict() for t, q in got.items()})
            unpriced += list(refused)
            notes += [f"{t}: {why}" for t, why in refused.items()]
        except Exception as exc:                        # NBP down, transport…
            unpriced += list(computed)
            notes.append(f"NBP rate unavailable ({type(exc).__name__})")

    if listed:
        got, missing, _ = price_source.fetch_quotes(listed)
        out.update({t: q.as_dict() for t, q in got.items()})
        unpriced += missing

    note = "fetched directly"
    if unpriced:
        note += f"; no quote: {', '.join(unpriced)}"
    if notes:
        note += " (" + "; ".join(notes) + ")"
    return out, note


def main() -> None:
    parser = argparse.ArgumentParser(description="Export portfolio status to Markdown.")
    parser.add_argument("-o", "--out", type=Path, default=snapshot.DEFAULT_OUT)
    parser.add_argument("--stdout", action="store_true", help="print, do not write")
    args = parser.parse_args()

    state = store.load()
    portfolio = store.active_portfolio(state)
    tickers = [h["ticker"] for h in store.holdings(portfolio)]

    got = prices_from_app()
    quotes, note = got if got else prices_direct(tickers)

    if args.stdout:
        rows, totals = snapshot.build(portfolio, quotes)
        print(snapshot.render(portfolio, rows, totals, note), end="")
        return

    # Asked for by hand, so an explicit destination may legitimately not exist
    # yet — unlike the automatic export, which must not invent directories.
    out = snapshot.export(quotes, note, args.out, create_dir=True)
    if out is None:
        # The default folder is someone else's notes layout, or this is a demo.
        sys.exit("Nothing written: a demo portfolio, or the default notes folder "
                 "does not exist here. Pass -o <file> to choose a destination.")
    rows, totals = snapshot.build(portfolio, quotes)
    print(f"Wrote: {out}  (read-only)")
    print(f"  {len(rows)} positions · {snapshot.money(totals['total'])} · prices: {note}")


if __name__ == "__main__":
    main()
