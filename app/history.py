"""Daily return of the portfolio against index benchmarks, for the Performance tab.

The one place the app looks at price *history*. Everything else values the
portfolio at today's quote; here each day's holdings, replayed from the ledger
(positions.py), are valued at that day's close.

The portfolio line is the **time-weighted price return**: each day's return is
that day's price profit over what was at risk, and money paid in or out is not
profit. Dividends and interest (the ledger's "income" rows) are left out of it,
as at myfund.pl, but lower the money paid in, so the value and profit charts
count them. That is
what makes it comparable with an index, which has no deposits either — a big
buy late in the year must not look like a gain. It is not the money-weighted
CAGR on the Overview cards, and the two will not match.

Daily closes come from CNBC's chart endpoint, over plain urllib like the quotes
in prices.py. Its bars are split-adjusted, so a share count taken from the
ledger before a split is scaled by the splits that came after it. Bonds have no
price history and are left out, with any ticker the source does not know.

Benchmarks are measured from the close *before* your first purchase, so the
first day's move counts, and WIG20 is converted from zloty to dollars with the
ECB's USD/PLN reference rate — the app is in dollars. Checked against myfund.pl
on the same ledger, all three match it to a few hundredths of a point.
"""

from __future__ import annotations

import json
import time
from datetime import date
import urllib.request
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor

import positions
import prices

CHART_URL = "https://ts-api.cnbc.com/harmony/app/charts/1Y.json?symbol="
BENCHMARKS = {"S&P 500": ".SPX", "NASDAQ 100": ".NDX", "WIG20": "WIG20"}
ZLOTY = {"WIG20"}             # benchmarks quoted in zloty, converted to dollars
ECB_URL = "https://api.frankfurter.dev/v1/%s..%s?base=USD&symbols=PLN"
RECENT_URL = "https://ts-api.cnbc.com/harmony/app/charts/5D.json?symbol="
CACHE_SECONDS = 3600

_cache: dict[str, tuple[float, dict[str, float]]] = {}


def clear() -> None:
    """Forget every downloaded series, so the next chart asks again."""
    _cache.clear()


def _bars(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": prices._UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["barData"]["priceBars"]


def _day(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"


def fetch_zloty(since: str) -> dict[str, float] | None:
    """ECB reference rate, zloty per dollar, by day — published after the European afternoon."""
    hit = _cache.get("USDPLN")
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        req = urllib.request.Request(ECB_URL % (since, date.today().isoformat()),
                                     headers={"User-Agent": prices._UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            rates = {d: float(v["PLN"]) for d, v in json.load(resp)["rates"].items()}
    except Exception:
        return None
    _cache["USDPLN"] = (time.time(), rates)
    return rates


def fetch_bars(symbol: str, regular_hours: bool = False) -> dict[str, float] | None:
    """ISO date -> close, or None when the source does not answer for this symbol.

    `regular_hours` keeps only the US regular session (09:30-16:00 New York) from
    the intraday bars: a share is valued at its session close, as the rest of the
    app does, not at a pre- or after-market print. Indices have no such prints.

    shortcut: the "1Y" chart returns about two years of daily bars; a ledger that
    starts earlier is clamped to the first bar, upgrade if that ever bites.
    """
    hit = _cache.get(symbol)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        bars = {_day(b["tradeTime"]): float(b["close"])
                for b in _bars(CHART_URL + urllib.request.quote(symbol))}
    except Exception:
        return None
    if not bars:
        return None
    # The daily chart lags by a trading day or two. The 5-day chart is intraday:
    # its last bar of a day is that day's close, or the latest price while the
    # market is open — good enough to reach today.
    try:
        newest = max(bars)
        for b in _bars(RECENT_URL + urllib.request.quote(symbol)):
            day = _day(b["tradeTime"])
            if regular_hours and not "093000" <= b["tradeTime"][8:] <= "160100":
                continue            # bars are stamped at the end of their five minutes
            if day > newest and date.fromisoformat(day).weekday() < 5:
                bars[day] = float(b["close"])
    except Exception:
        pass
    _cache[symbol] = (time.time(), bars)
    return bars


def _on(bars: dict[str, float], days: list[str]) -> list[float | None]:
    """The close on each day, carried forward over days the market was shut."""
    out, last, dates = [], None, sorted(bars)
    i = 0
    for day in days:
        while i < len(dates) and dates[i] <= day:
            last = bars[dates[i]]
            i += 1
        out.append(last)
    return out


def series(transactions: list[dict], bars: dict[str, dict[str, float]],
           benches: dict[str, dict[str, float]]) -> dict:
    """Pure: ledger and daily closes in, percent curves out, plus the value of what
    was held and the net money paid in on each day. Their difference is the overall
    profit of the Overview: income lowers the money paid in, so it counts as profit.

    A ticker without bars is left out together with its transactions — valuing
    it at zero would book its cost as a loss.
    """
    trades = [t for t in transactions if t.get("kind") != "income"]
    left_out = sorted({str(t.get("ticker", "")).strip().upper() for t in trades} - set(bars))
    ledger = [t for t in trades if str(t.get("ticker", "")).strip().upper() in bars]
    first = min((str(t.get("date") or "") for t in ledger if t.get("date")), default=None)
    if not first:
        return {"dates": [], "portfolio": [], "benchmarks": {}, "left_out": left_out}

    days = sorted({d for b in [*bars.values(), *benches.values()] for d in b if d >= first})
    if not days:
        return {"dates": [], "portfolio": [], "benchmarks": {}, "left_out": left_out}

    # Income (dividends, interest, net of tax) lowers the net money paid in, so
    # it is profit in the value and profit charts, as at myfund. The return line
    # is the price return and leaves it out.
    earned = [0.0] * len(days)
    for t in transactions:
        who, day = str(t.get("ticker", "")).strip().upper(), str(t.get("date") or "")
        if t.get("kind") == "income" and day >= first and (not who or who in bars):
            earned[min(bisect_left(days, day), len(days) - 1)] += float(t.get("amount") or 0)

    # Per ticker: what the book held after each transaction, and the split ratio
    # of that transaction (1 when it was not a split). Per day: money in or out.
    events: dict[str, list[tuple[str, float, float]]] = {}
    flow = [0.0] * len(days)
    before: dict[str, float] = {}
    for tx, book, _ in positions.replay(ledger):
        ticker = str(tx["ticker"]).strip().upper()
        ratio = float(tx.get("ratio") or 1) if str(tx.get("kind")).lower() == "split" else 1.0
        day = str(tx.get("date") or "")
        events.setdefault(ticker, []).append((day, book["shares"], ratio))
        moved = book["shares"] - before.get(ticker, 0.0)
        before[ticker] = book["shares"]
        if ratio == 1.0 and day:
            flow[min(bisect_left(days, day), len(days) - 1)] += moved * float(tx.get("price") or 0)

    def held(ticker: str, day: str) -> float:
        shares, later = 0.0, 1.0
        for when, after, ratio in events[ticker]:
            if when <= day:
                shares = after
            else:
                later *= ratio
        return shares * later

    closes_src = [b for t, b in bars.items() if t in events]
    closes = {t: _on(b, days) for t, b in bars.items() if t in events}
    value = [sum(held(t, d) * c[i] for t, c in closes.items() if c[i] is not None)
             for i, d in enumerate(days)]

    # Money paid in on a day counts as invested from the start of that day, so
    # the day's return is its profit over what was at risk: yesterday's value plus
    # what was paid in. The first day has no yesterday, and is measured only when
    # the bars reach back past the first purchase; otherwise the ledger began
    # before the data and day one's value is not all new money.
    fresh = min(min(b) for b in closes_src) < first
    cum, curve, prev = 1.0, [], 0.0
    for i, v in enumerate(value):
        base = prev + flow[i]
        if (i or fresh) and base > 0:
            cum *= v / base
        curve.append(round((cum - 1) * 100, 3))
        prev = v

    invested, paid = [], 0.0
    for f, e in zip(flow, earned):
        paid += f - e
        invested.append(round(paid, 2))

    out = {}
    for name, b in benches.items():
        px = _on(b, days)
        prior = [d for d in b if d < first]
        ref = b[max(prior)] if prior else px[0]
        if ref:
            out[name] = [round((p / ref - 1) * 100, 3) if p else None for p in px]
    return {"dates": days, "portfolio": curve, "benchmarks": out, "left_out": left_out,
            "value": [round(v, 2) for v in value], "invested": invested}


def build(transactions: list[dict], skip: set[str]) -> dict:
    """Fetch what the ledger and the benchmarks need, then draw the curves."""
    tickers = sorted({str(t.get("ticker", "")).strip().upper() for t in transactions} - skip)
    symbols = [prices._cnbc_symbol(t) for t in tickers] + list(BENCHMARKS.values())
    with ThreadPoolExecutor(8) as pool:
        got = dict(zip(symbols, pool.map(fetch_bars, symbols, [True] * len(tickers) + [False] * len(BENCHMARKS))))
    bars = {t: got[prices._cnbc_symbol(t)] for t in tickers if got[prices._cnbc_symbol(t)]}
    benches = {n: got[s] for n, s in BENCHMARKS.items() if got[s]}
    for name in ZLOTY & set(benches):
        days = sorted(benches[name])
        rate = fetch_zloty(days[0])
        if rate:
            benches[name] = {d: benches[name][d] / r for d, r in zip(days, _on(rate, days)) if r}
        else:
            del benches[name]       # a zloty index against dollars, unconverted, would lie
    result = series([t for t in transactions if str(t.get("ticker", "")).strip().upper() not in skip],
                    bars, benches)
    result["parked"] = sorted(skip & {str(t.get("ticker", "")).strip().upper() for t in transactions})
    result["missing_benchmarks"] = [n for n in BENCHMARKS if n not in benches]
    return result
