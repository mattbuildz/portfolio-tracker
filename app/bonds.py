"""Retail savings bonds — priced from emission terms, outside equity weights.

A Polish retail treasury bond (TOS and its siblings) is not traded. There is no
venue to ask what it costs: its value on any day follows from the emission
terms alone (nominal, fixed rate, purchase day, annual capitalisation). That
makes it a price with a source (CONTEXT.md); the source is a formula, labelled
as one.

It sits in the portfolio as value — cash parked at a known rate — but it does
**not** take part in allocation. Target weights, current weights, drift and
rebalance plans are equity-only. Mixing a bond into those percentages would
make every equity look underweight by the bond's share of total wealth, which
is exactly the wrong signal for anyone reading the status export.

From the issuer's emission letter for the bond series:

  - Interest accrues from the day of purchase, period to period on that day.
  - Year one: interest on the nominal. Later years: on the nominal plus the
    interest capitalised at the previous anniversary.

What this deliberately does not do: Belka tax (gross, like every equity row),
the early-redemption fee (only paid on early exit), or variable-rate bonds.
"""

from __future__ import annotations

from datetime import date, datetime

import prices as price_source

PORTFOLIO_CURRENCY = "USD"
SUPPORTED_CURRENCY = "PLN"
FIXED = "fixed-annual-capitalisation"


class SpecError(ValueError):
    """The stored terms cannot be valued — missing, malformed, or not fixed-rate."""


def _day(value, field: str) -> date:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise SpecError(f"{field}: expected a YYYY-MM-DD date, got {value!r}") from None


def read(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("expected an object")
    kind = str(spec.get("kind") or "").strip()
    if kind != FIXED:
        raise SpecError(f"kind {kind!r} is not {FIXED!r}")
    currency = str(spec.get("currency") or "").strip().upper()
    if currency != SUPPORTED_CURRENCY:
        raise SpecError(f"currency {currency!r} is not {SUPPORTED_CURRENCY}")
    nominal = float(spec.get("nominal") or 0)
    rate = float(spec.get("rate") or 0)
    if nominal <= 0 or rate <= 0:
        raise SpecError("nominal and rate must be more than zero")
    bought, matures = _day(spec.get("bought"), "bought"), _day(spec.get("matures"), "matures")
    if matures <= bought:
        raise SpecError("matures must fall after bought")
    return {"kind": kind, "currency": currency, "nominal": nominal, "rate": rate,
            "bought": bought, "matures": matures,
            "series": str(spec.get("series") or "").strip()}


def _anniversary(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, month=2, day=28)


def value(spec: dict, on: date | None = None) -> float:
    """Gross value of one bond on `on`, in its own currency. Stops at maturity."""
    terms = read(spec)
    on = min(on or date.today(), terms["matures"])
    if on <= terms["bought"]:
        return terms["nominal"]
    base, years = terms["nominal"], 0
    while _anniversary(terms["bought"], years + 1) <= on:
        base *= 1 + terms["rate"]
        years += 1
    opened = _anniversary(terms["bought"], years)
    if opened >= terms["matures"]:
        return base
    closes = _anniversary(terms["bought"], years + 1)
    return base + base * terms["rate"] * (on - opened).days / (closes - opened).days


def quote(ticker: str, spec: dict, fx: price_source.FxRate,
          on: date | None = None) -> price_source.Quote:
    if fx.pair != f"{PORTFOLIO_CURRENCY}/{SUPPORTED_CURRENCY}":
        raise SpecError(f"cannot convert {SUPPORTED_CURRENCY} with a {fx.pair} rate")
    return price_source.Quote(
        ticker=ticker,
        price=value(spec, on) / fx.rate,
        currency=PORTFOLIO_CURRENCY,
        as_of=fx.as_of,
        as_of_kind="accrual",
        fetched_at=price_source.now(),
        source=f"accrual · {fx.source}",
    )


def quotes(specs: dict[str, dict], fx: price_source.FxRate,
           on: date | None = None) -> tuple[dict[str, price_source.Quote], dict[str, str]]:
    found, refused = {}, {}
    for ticker, spec in specs.items():
        try:
            found[ticker] = quote(ticker, spec, fx, on)
        except (SpecError, ValueError, ZeroDivisionError) as exc:
            refused[ticker] = str(exc)
    return found, refused
