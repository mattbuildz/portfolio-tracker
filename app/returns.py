"""Overall profit, total return and annualised return (CAGR) of a ledger.

Pure, like positions.py: transactions and a current value go in, numbers come
out — no disk, no network, no clock (the caller says what "today" is).

**Overall profit** is realised plus unrealised profit, the one sum CONTEXT.md
used to forbid: it answers "how much richer am I than the money I put in", and
that is a question worth a number. The two parts are still reported apart
everywhere else; this is the sum, not a replacement for them.

    overall profit = value now + money received from sales - money paid for buys

which equals unrealised + realised, because a sale's realised profit is its
proceeds minus the average cost of the shares that left. **Return** is that
profit over everything ever paid for buys.

**Annualised return** is money-weighted (XIRR), not `(end / start) ** (1 / years)`.
Buys arrive over months; a euro paid in last week has not had a year to grow,
and dividing the profit by the full span would understate the rate. XIRR finds
the single yearly rate at which every dated payment in and out, plus the value
today, nets to zero.

Fees follow the rest of the app: sale fees reduce the proceeds (as they reduce
realised profit), buy fees are not part of the cost.

**Periods.** `max` runs from the first dated transaction. `1y`/`3y`/`5y` look
back that far from today. If the look-back reaches past the first transaction
the whole history is shorter than the window, so it is the same figure as `max`
and is flagged `clamped`. If it lands *inside* the history the answer would need
the portfolio's value on that date — the app does not keep price history — so
it is reported as unavailable instead of guessed.
"""

from __future__ import annotations

from datetime import date, timedelta

import positions

PERIODS = ("max", "1y", "3y", "5y")
# Annualising a few days of movement turns noise into a four-digit percentage.
MIN_DAYS = 30
YEAR_DAYS = 365.0


def _parse(day) -> date | None:
    try:
        return date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):
        return None


def _flows(transactions: list[dict]) -> tuple[list[tuple[date | None, float]], float, float]:
    """Dated cash flows (buy negative, sale positive), total bought, total received."""
    flows, bought, received = [], 0.0, 0.0
    held: dict[str, float] = {}      # shares before each transaction, to clamp a sale like the fold does
    for tx, book, _ in positions.replay(transactions):
        ticker = str(tx.get("ticker", "")).strip().upper()
        before = held.get(ticker, 0.0)
        held[ticker] = book["shares"]
        kind = str(tx.get("kind", "buy")).lower()
        qty = float(tx.get("shares") or 0)
        price = float(tx.get("price") or 0)
        fee = float(tx.get("fee") or 0)
        when = _parse(tx.get("date"))
        if kind == "buy":
            amount = qty * price
            bought += amount
            flows.append((when, -amount))
        elif kind == "sell":
            amount = min(qty, before) * price - fee
            received += amount
            flows.append((when, amount))
    return flows, bought, received


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """The yearly rate at which dated flows net to zero, or None if there is none."""
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)
    pairs = [((d - t0).days / YEAR_DAYS, a) for d, a in flows]

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for t, a in pairs)

    lo, hi = -0.999999, 1e6
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def compute(transactions: list[dict], value: float, today: date,
            realised_opening: float = 0.0, period: str = "max") -> dict:
    """Profit, return and CAGR over one period.

    `value` is what the shares still held are worth now; the caller leaves out
    any holding it could not price, and its transactions with it.
    """
    flows, bought, received = _flows(transactions)
    profit = value + received - bought + realised_opening
    out = {
        "period": period,
        "profit": profit,
        "invested": bought,
        "return_pct": profit / bought * 100 if bought > 0 else None,
        "cagr_pct": None,
        "cagr_simple_pct": None,
        "since": None,
        "days": None,
        "clamped": False,
        "note": None,
    }
    dated = [d for d, _ in flows if d is not None]
    if not dated:
        out["note"] = "no dated transactions" if flows else "nothing bought yet"
        return out

    first = min(dated)
    out["since"] = first.isoformat()
    window_start = first
    if period != "max":
        years = int(period[:-1])
        try:
            window_start = today.replace(year=today.year - years)
        except ValueError:          # 29 February
            window_start = today.replace(year=today.year - years, day=28)
        if window_start <= first:
            window_start = first
            out["clamped"] = True
        else:
            out["since"] = window_start.isoformat()
            out["profit"] = out["return_pct"] = None
            out["invested"] = None
            out["note"] = (f"needs the portfolio's value on {window_start.isoformat()} — "
                           "the app keeps no price history")
            return out

    days = (today - window_start).days
    out["days"] = days
    # Undated opening balances are assumed held from the first dated day.
    dated_flows = [(d or first, a) for d, a in flows]
    if value > 0:
        dated_flows.append((today, value))
    if days < MIN_DAYS:
        out["note"] = f"under {MIN_DAYS} days of history — too short to annualise"
        return out
    rate = xirr(dated_flows)
    out["cagr_pct"] = rate * 100 if rate is not None else None
    # The plain return carried to a year, as if all the money had been in from
    # the first day: what a calculator without dated cash flows would say.
    if bought > 0 and profit / bought > -1:
        out["cagr_simple_pct"] = ((1 + profit / bought) ** (YEAR_DAYS / days) - 1) * 100
    return out


def all_periods(transactions: list[dict], value: float, today: date,
                realised_opening: float = 0.0) -> dict[str, dict]:
    return {p: compute(transactions, value, today, realised_opening, p) for p in PERIODS}
