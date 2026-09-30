"""Folding transactions into positions.

CONTEXT.md is explicit that a transaction is the fact and a position is only a
state derived from it. This module is that derivation, and nothing else: no
disk, no network, no globals. Give it a list of transactions, get back what is
held and at what average cost.

The one rule worth stating out loud, because every broker follows it and it
still surprises people: **a sale does not move the average price.** What you
paid for the shares that stay is not changed by what you got for the ones that
left. Selling lowers the count and the total cost in the same proportion, so
the quotient — the average — comes out unchanged. The difference between the
sale price and that average is realised profit, and it is kept apart.

Commission is deliberately *not* folded into the average price. A broker's
"open price" is the price of the share, and matching what the statement says is
worth more here than a marginally truer cost basis; fees are summed separately
so nothing is lost.
"""

from __future__ import annotations

# Shares are floats, so "sell everything" of a fractional holding can miss the
# zero by a rounding crumb. Anything under this is no shares at all.
DUST = 1e-9


def _order(transactions: list[dict]) -> list[dict]:
    """Chronological, and stable within a day.

    Two trades on the same date must fold in the order they were entered — for
    a buy and a sell of one company on one day, the other order would compute a
    different realised profit.
    """
    return sorted(enumerate(transactions), key=lambda pair: (str(pair[1].get("date") or ""), pair[0]))


def replay(transactions: list[dict]):
    """Walk the ledger in order, yielding what each transaction did.

    Everything else in this module is a reading of this walk. Keeping it in one
    place means the average price shown on a row and the realised profit shown
    against a sale can never drift apart: they are the same arithmetic, visited
    once.

    Yields `(transaction, book, realised_delta)` where `book` is the running
    state of that company *after* the transaction — the same dict each time, so
    copy it if you need to keep it.
    """
    books: dict[str, dict] = {}
    for _, tx in _order(transactions):
        ticker = str(tx.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        book = books.setdefault(ticker, {"shares": 0.0, "cost": 0.0, "realised": 0.0, "fees": 0.0})
        kind = str(tx.get("kind", "buy")).lower()
        qty = float(tx.get("shares") or 0)
        price = float(tx.get("price") or 0)
        fee = float(tx.get("fee") or 0)
        gain = 0.0

        if kind == "buy":
            book["shares"] += qty
            book["cost"] += qty * price
            book["fees"] += fee

        elif kind == "sell":
            # Clamped rather than rejected: this runs over whatever is on disk,
            # including a file edited by hand. Validation belongs at the door,
            # not here — a fold that raises would take the whole app down.
            qty = min(qty, book["shares"])
            avg = book["cost"] / book["shares"] if book["shares"] > DUST else 0.0
            gain = qty * (price - avg) - fee
            book["realised"] += gain
            book["shares"] -= qty
            book["cost"] = avg * book["shares"]     # average survives untouched
            book["fees"] += fee

        elif kind == "split":
            # No money moves: the count changes, the cost basis does not, and
            # the average price falls out of the division on its own.
            ratio = float(tx.get("ratio") or 0)
            if ratio > 0:
                book["shares"] *= ratio

        yield tx, book, gain


def fold(transactions: list[dict]) -> tuple[dict[str, dict], float]:
    """Return positions keyed by ticker, and the portfolio's realised profit.

    Positions with no shares left are dropped: a company sold out of entirely
    is not a holding. Its realised profit stays in the total — that is the
    whole point of realised profit being a portfolio-level figure and not a
    row-level one.
    """
    books: dict[str, dict] = {}
    for tx, book, _ in replay(transactions):
        books[str(tx["ticker"]).strip().upper()] = book

    positions = {}
    realised = 0.0
    for ticker, book in books.items():
        realised += book["realised"]
        if book["shares"] <= DUST:
            continue
        positions[ticker] = {
            "shares": book["shares"],
            "avg_price": book["cost"] / book["shares"],
            "cost": book["cost"],
            "fees": book["fees"],
        }
    return positions, realised


def realised_by_transaction(transactions: list[dict]) -> dict[str, float]:
    """What each individual sale locked in, keyed by transaction id.

    A sale's realised profit depends on every transaction before it, so it
    cannot be read off the row itself — it has to be replayed.
    """
    return {tx.get("id"): gain for tx, _, gain in replay(transactions)
            if str(tx.get("kind", "")).lower() == "sell" and tx.get("id")}


def running(transactions: list[dict]) -> dict[str, dict]:
    """The state of a company's position immediately after each transaction.

    This is what lets a row be expanded to show an average price *building up*
    rather than only its final value. It is the same replay the totals come
    from, so the last line of an expanded company always equals the figure on
    its collapsed row — they cannot disagree, because they are one walk.
    """
    out: dict[str, dict] = {}
    for seq, (tx, book, gain) in enumerate(replay(transactions)):
        if not tx.get("id"):
            continue
        out[tx["id"]] = {
            # The position in the replay, not in the file. Two trades on one day
            # fold in the order they were entered, and a reader ordering them by
            # date alone would put a running share count next to the wrong row —
            # the figures right, the story wrong. Anything showing this sequence
            # sorts by `seq` and inherits the fold's order exactly.
            "seq": seq,
            "shares_after": book["shares"],
            "avg_after": book["cost"] / book["shares"] if book["shares"] > DUST else 0.0,
            "realised": gain,
        }
    return out


def can_sell(transactions: list[dict], ticker: str, qty: float) -> tuple[bool, float]:
    """How many shares are actually held, and whether this sale fits inside it."""
    positions, _ = fold(transactions)
    held = positions.get(ticker.strip().upper(), {}).get("shares", 0.0)
    return qty <= held + 1e-9, held
