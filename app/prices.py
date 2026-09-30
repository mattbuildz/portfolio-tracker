"""Price snapshots for US equities, and the exchange rate a foreign holding needs.

A snapshot is a price *and* the time it was fetched (CONTEXT.md). Nothing in
here returns a bare number, and nothing returns a price it could not attribute
to a source.

Source order follows research/price-source.md:

1. Yahoo chart endpoint, but only when ``curl_cffi`` is installed — Yahoo
   fingerprints the TLS handshake and refuses every stdlib client.
2. CNBC quote endpoint over plain urllib. Zero dependencies, one batched call.
3. Neither: the caller keeps the last known snapshot and says so on screen.

`fetch_fx` is separate: the NBP reference rate that turns a zloty bond value
into the dollars the rest of the app is denominated in.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

CNBC_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/"
            "symbolType/symbol")
YAHOO_URL = "https://query2.finance.yahoo.com/v8/finance/chart/"
NBP_URL = "https://api.nbp.pl/api/exchangerates/rates/a/"


@dataclass
class Quote:
    ticker: str
    price: float
    currency: str
    as_of: str            # when the venue printed the price: a timestamp, or a
                          # bare date when all the source gives is a close
    as_of_kind: str       # "trade" | "close" | "accrual" (computed, not quoted)
    fetched_at: str       # ISO 8601, UTC — when we asked
    source: str           # "yahoo" | "cnbc" | "accrual · nbp"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FxRate:
    """How many units of the quote currency one unit of the base currency buys."""
    pair: str             # "USD/PLN"
    rate: float
    as_of: str            # the day the rate was published
    source: str           # "nbp"


class FetchError(Exception):
    """Every source failed. Distinct from 'this ticker does not exist'."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_now = now

def _cnbc_symbol(ticker: str) -> str:
    # Yahoo writes BRK-B, CNBC writes BRK.B.
    return ticker.replace("-", ".").upper()


# --------------------------------------------------------------------------
# CNBC — the zero-dependency path
# --------------------------------------------------------------------------

def _fetch_cnbc(tickers: list[str]) -> tuple[dict[str, Quote], list[str]]:
    params = {
        "symbols": "|".join(_cnbc_symbol(t) for t in tickers),
        "requestMethod": "itv",
        "noform": "1",
        "partnerId": "2",
        "fund": "1",
        "exthrs": "1",
        "output": "json",
        "events": "1",
    }
    req = urllib.request.Request(CNBC_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.load(resp)

    quotes = payload.get("FormattedQuoteResult", {}).get("FormattedQuote")
    if not quotes:
        raise FetchError("CNBC returned no quote block")

    by_cnbc_symbol = {t: _cnbc_symbol(t) for t in tickers}
    found: dict[str, Quote] = {}
    fetched = _now()

    for item in quotes:
        # code 1 is CNBC's sentinel for "no such symbol" — not a transport
        # failure, so it must not poison the whole fetch.
        if item.get("code") != 0 or item.get("last") in (None, ""):
            continue
        symbol = str(item.get("symbol", "")).upper()
        ticker = next((t for t, s in by_cnbc_symbol.items() if s == symbol), symbol)

        # The regular session is what the portfolio is valued at, the way a
        # broker shows it. Outside trading hours `last` is that session's
        # close and `last_time` is a bare date, so the price and its stamp
        # must be taken from the same block — pairing a closing price with a
        # pre-market trade time would make yesterday's number look live.
        price = float(str(item["last"]).replace(",", ""))
        raw_time = item.get("last_time") or ""
        as_of, kind = _stamp(raw_time, fetched)

        found[ticker] = Quote(
            ticker=ticker,
            price=price,
            currency=item.get("currencyCode") or "USD",
            as_of=as_of,
            as_of_kind=kind,
            fetched_at=fetched,
            source="cnbc",
        )

    missing = [t for t in tickers if t not in found]
    return found, missing


def _stamp(value: str, fallback: str) -> tuple[str, str]:
    """CNBC times come in two shapes, and they mean different things.

    A full offset stamp is an actual trade. A bare date is a session close —
    returned as the date it is, never widened into a time the source never
    gave us.
    """
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds"), "trade"
    except (ValueError, TypeError):
        pass
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value, "close"
    except (ValueError, TypeError):
        return fallback, "trade"


# --------------------------------------------------------------------------
# Yahoo — only reachable with a TLS-impersonating client
# --------------------------------------------------------------------------

def yahoo_available() -> bool:
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return False
    return True


def _fetch_yahoo(tickers: list[str]) -> tuple[dict[str, Quote], list[str]]:
    from curl_cffi import requests as cffi_requests  # type: ignore

    found: dict[str, Quote] = {}
    fetched = _now()
    # There is no working Yahoo batch endpoint (v7 is crumb-gated), so this is
    # one call per ticker by necessity.
    for ticker in tickers:
        try:
            resp = cffi_requests.get(
                YAHOO_URL + urllib.parse.quote(ticker),
                params={"range": "1d", "interval": "1d"},
                impersonate="chrome",
                timeout=12,
            )
            if resp.status_code != 200:
                continue
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            if price is None:
                continue
            stamp = meta.get("regularMarketTime")
            as_of = (datetime.fromtimestamp(stamp, timezone.utc)
                     .isoformat(timespec="seconds")) if stamp else fetched
            found[ticker] = Quote(
                ticker=ticker,
                price=float(price),
                currency=meta.get("currency") or "USD",
                as_of=as_of,
                as_of_kind="trade",
                fetched_at=fetched,
                source="yahoo",
            )
        except Exception:
            # One ticker failing is not the fetch failing; the caller sees it
            # in `missing` and the CNBC pass picks it up.
            continue

    missing = [t for t in tickers if t not in found]
    if not found and tickers:
        raise FetchError("Yahoo returned nothing for any ticker")
    return found, missing


# --------------------------------------------------------------------------
# NBP — reference rate for holdings not priced in dollars
# --------------------------------------------------------------------------

def fetch_fx(code: str = "USD") -> FxRate:
    """How many zloty one unit of `code` buys, from the NBP reference table A.

    The stamp is NBP's `effectiveDate`, not today — a Sunday ask returns Friday.
    """
    url = f"{NBP_URL}{urllib.parse.quote(code.lower())}/last/1/?format=json"
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.load(resp)
    rates = payload.get("rates") or []
    if not rates:
        raise FetchError(f"NBP returned no rate for {code}")
    rate = float(rates[-1]["mid"])
    if rate <= 0:
        raise FetchError(f"NBP returned a rate of {rate} for {code}")
    return FxRate(pair=f"{code.upper()}/PLN", rate=rate,
                  as_of=str(rates[-1].get("effectiveDate") or "")[:10], source="nbp")


# --------------------------------------------------------------------------

def fetch_quotes(tickers: list[str]) -> tuple[dict[str, Quote], list[str], list[str]]:
    """Return (quotes, unpriced tickers, notes about what happened)."""
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers:
        return {}, [], ["nothing to price"]

    notes: list[str] = []
    quotes: dict[str, Quote] = {}
    missing = list(tickers)
    answered = False          # did any source actually respond?

    if yahoo_available():
        try:
            quotes, missing = _fetch_yahoo(missing)
            answered = True
            notes.append(f"yahoo: {len(quotes)}/{len(tickers)}")
        except Exception as exc:
            notes.append(f"yahoo failed ({type(exc).__name__}) — falling back")
            missing = list(tickers)
    else:
        notes.append("yahoo skipped: curl_cffi not installed")

    if missing:
        try:
            more, missing = _fetch_cnbc(missing)
            answered = True
            quotes.update(more)
            notes.append(f"cnbc: {len(more)}/{len(missing) + len(more)}")
        except (urllib.error.URLError, OSError, ValueError, FetchError) as exc:
            notes.append(f"cnbc failed ({type(exc).__name__})")

    # A source that answered "no such symbol" is a working fetch with a bad
    # ticker in it — the one distinction research/price-source.md picked these
    # sources for. Only silence from every source is a failed fetch.
    if not answered:
        raise FetchError("; ".join(notes))
    return quotes, missing, notes


if __name__ == "__main__":
    start = time.time()
    q, miss, log = fetch_quotes(["AAPL", "NVDA", "BRK-B", "NOTATICKER"])
    for t, quote in q.items():
        print(f"{t:10} {quote.price:>10.2f} {quote.currency}  "
              f"{quote.as_of} ({quote.as_of_kind})  {quote.source}")
    print("unpriced:", miss)
    print("notes:", log, f"({time.time() - start:.1f}s)")
