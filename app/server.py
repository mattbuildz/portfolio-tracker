"""Localhost server for the portfolio tracker.

Standard library only: `python3 app/server.py` and it runs. Prices come from
app/prices.py; portfolios from app/store.py.

Prices are fetched once at startup, on a background thread so the page is never
blocked by the network, and again whenever the Fetch prices button is pressed.
Both paths go through exactly the same function.

Environment variables, all optional:

  PORTFOLIO_DATA_DIR        where portfolios.json lives (store.data_dir).
  PORTFOLIO_LOCAL_DIR       where the API key and fetched transcripts live
                            (store.local_dir); defaults to the data folder.
  PORTFOLIO_SNAPSHOT_FILE   where the Markdown status export goes (snapshot.py).
  PORTFOLIO_NO_SNAPSHOT     set: never write that export.
  PORTFOLIO_IDLE_MINUTES    exit after this long without a request; 0 = never.
  PORTFOLIO_NO_BROWSER      set: do not open a browser tab on start.
  PORTFOLIO_NO_APP_INSTALL  set: do not install the desktop launcher.

`--demo` runs a throwaway copy of the made-up portfolio in `demo/` on its own
port. A data folder is treated as a demo whenever it carries the `DEMO` marker
(store.is_demo), not only when the flag was given.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bonds
import launcher
import positions
import prices as price_source
import snapshot
import store
import transcripts

HOST = "127.0.0.1"
PORT = 8777
DEMO_PORT = 8778
DEMO = False                # set in main() from store.is_demo(); used for the banner line
DEMO_SEED = Path(__file__).resolve().parent.parent / "demo"
DEMO_WORK: Path | None = None   # the temp copy enter_demo made — the only thing it may delete
STATIC = Path(__file__).parent / "static"

# Closing the browser tab cannot stop a server — the page has no way to say
# "that was the last window". Idleness can: no request for this long and the
# process exits on its own. 0 keeps it running until it is killed.
IDLE_MINUTES = float(os.environ.get("PORTFOLIO_IDLE_MINUTES", "30"))


class PriceCache:
    """Last known prices, with the story of how the last fetch went.

    Keeping the previous snapshot on a failed fetch is deliberate: a portfolio
    valued at stale prices and labelled stale is useful, an empty screen is not.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.quotes: dict[str, dict] = {}
        self.fetched_at: str | None = None
        self.status: str = "never"      # never | ok | partial | failed | fetching
        self.notes: list[str] = []
        self.unpriced: list[str] = []

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "fetched_at": self.fetched_at,
                "status": self.status,
                "notes": list(self.notes),
                "unpriced": list(self.unpriced),
                "yahoo_available": price_source.yahoo_available(),
            }

    def mark_fetching(self) -> None:
        with self._lock:
            self.status = "fetching"

    def refresh(self, tickers: list[str]) -> dict:
        """Price equities from the market; bonds from their emission terms.

        The two paths are independent so a dead quote venue does not stop a bond
        being valued, and a missing NBP rate does not blank the equities.
        """
        self.mark_fetching()
        specs = store.bond_specs(store.load())
        computed = {t: specs[t] for t in tickers if t in specs}
        quotes, unpriced, notes = self._bond_prices(computed)
        listed = [t for t in tickers if t not in specs]
        failed = False
        try:
            got, missing, market_notes = price_source.fetch_quotes(listed)
            quotes.update(got)
            unpriced += missing
            notes += market_notes
        except price_source.FetchError as exc:
            failed, notes = True, notes + [str(exc)]
        except Exception as exc:
            failed, notes = True, notes + [f"{type(exc).__name__}: {exc}"]

        with self._lock:
            self.quotes.update({t: q.as_dict() for t, q in quotes.items()})
            if quotes:
                self.fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.notes = notes
            if failed:
                self.status = "failed"
                self.unpriced = [t for t in tickers if t not in self.quotes]
            else:
                self.unpriced = unpriced
                self.status = "partial" if unpriced else "ok"
        self.export_snapshot("price fetch")
        return self.snapshot()

    def _bond_prices(self, specs: dict[str, dict]):
        if not specs:
            return {}, [], []
        try:
            fx = price_source.fetch_fx()
        except Exception as exc:
            return {}, list(specs), [f"nbp failed ({type(exc).__name__}) — "
                                     f"{len(specs)} bond(s) unpriced"]
        quotes, refused = bonds.quotes(specs, fx)
        notes = [f"accrual: {len(quotes)}/{len(specs)} at {fx.pair} "
                 f"{fx.rate:g} of {fx.as_of}"]
        notes += [f"{ticker}: {why}" for ticker, why in refused.items()]
        return quotes, list(refused), notes

    def export_snapshot(self, reason: str, require_fresh: bool = True) -> None:
        """Write the Markdown status export, so notes stay in sync with this screen.

        Two things move what the snapshot says, and each calls this: prices,
        which only change on a fetch, and the ledger, which changes on every
        POST. Opening the app fetches, so opening the app refreshes the file.

        `require_fresh` is the difference between them. After a *failed fetch*
        nothing is written: the cache still holds the last good prices, and
        rewriting the file then would present week-old quotes as the outcome of
        a fetch that did not happen. After a *trade* the file is written even if
        the last fetch failed, because the share count genuinely did change and
        withholding that leaves a worse lie in place — an old position size. The
        prices carry their own age in `prices_as_of` either way, and the note says
        plainly that they are the last ones that arrived.

        Nothing in here may interrupt the app: this runs on the fetch thread and
        on request threads, and the portfolio must stay usable with the export folder
        unreachable, renamed or mid-sync. A failure becomes a note on the price
        line, which is where the user already looks for how the fetch went.
        """
        if os.environ.get("PORTFOLIO_NO_SNAPSHOT") or store.is_demo():
            return          # a made-up portfolio must never overwrite the real status file
        with self._lock:
            fresh = self.status in ("ok", "partial")
            if require_fresh and not fresh:
                return
            if not self.quotes:
                return          # nothing priced yet: an export would value it at zero
            quotes = dict(self.quotes)
            note = f"app, {reason}"
            if not fresh:
                note += " (last good quotes; fresh fetch failed)"
        try:
            snapshot.export(quotes, note)
        except Exception as exc:
            with self._lock:
                self.notes = [*self.notes, f"snapshot not written: {exc}"]

    def price_for(self, ticker: str) -> dict | None:
        with self._lock:
            return self.quotes.get(ticker)


CACHE = PriceCache()
LAST_REQUEST = time.monotonic()


def state_payload() -> dict:
    state = store.load()
    active = store.active_portfolio(state)
    parked = set(store.bond_specs(state))
    rows = []
    for h in store.holdings(active):
        quote = CACHE.price_for(h["ticker"])
        price = quote["price"] if quote else None
        value = price * h["shares"] if price is not None else None
        basis = h["avg_price"] * h["shares"]
        is_parked = h["ticker"] in parked
        rows.append({
            **h,
            # A parked bond has no place in allocation policy — force target off
            # so a stale number never shows up as drift against nothing.
            "target": 0.0 if is_parked else h["target"],
            "parked": is_parked,
            "price": price,
            "as_of": quote["as_of"] if quote else None,
            "as_of_kind": quote["as_of_kind"] if quote else None,
            "source": quote["source"] if quote else None,
            "value": value,
            "basis": basis,
            "pl": (value - basis) if value is not None else None,
        })
    snap = CACHE.snapshot()
    # What is unpriced is a fact about the rows on screen, not about the last
    # fetch: a ticker deleted since then must stop being reported.
    snap["unpriced"] = [r["ticker"] for r in rows if r["price"] is None]
    if snap["status"] == "partial" and not snap["unpriced"]:
        snap["status"] = "ok"

    # Newest first: the ledger is read as history, and history is read backwards.
    # What a sale realised depends on everything before it, so it is replayed
    # here rather than left for the page to guess at.
    # Each row carries what the position looked like straight after it, so an
    # expanded company can show its average price building up one purchase at
    # a time. Replayed here, from the same walk as every other figure.
    running = positions.running(active["transactions"])
    ledger = [t | running.get(t["id"], {}) for t in
              sorted(active["transactions"],
                     key=lambda t: (t.get("date") or "", t.get("id") or ""), reverse=True)]

    return {
        "portfolios": [{"id": pf["id"], "name": pf["name"], "count": len(store.holdings(pf))}
                       for pf in state["portfolios"]],
        "active": state["active"],
        "active_name": active["name"],
        "holdings": rows,
        "transactions": ledger,
        "realised": store.realised(active),
        "snapshot": snap,
        "data_file": str(store.path()),
        "demo": store.is_demo(),
        "profiles": store.profiled_tickers(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PortfolioLocal/0.1"

    # ---- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):      # one tidy line, not two
        print(f"  {self.command} {self.path} — {fmt % args}")

    def _touch(self) -> None:
        global LAST_REQUEST
        LAST_REQUEST = time.monotonic()

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # ---- routes ---------------------------------------------------------
    def do_GET(self) -> None:
        self._touch()
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if route == "/app.js":
            return self._file("app.js", "text/javascript; charset=utf-8")
        if route == "/company.js":
            return self._file("company.js", "text/javascript; charset=utf-8")
        if route == "/company.css":
            return self._file("company.css", "text/css; charset=utf-8")
        if route == "/theme.css":
            return self._file("theme.css", "text/css; charset=utf-8")
        if route == "/app.css":
            return self._file("app.css", "text/css; charset=utf-8")
        if route in ("/favicon.svg", "/favicon.ico"):
            return self._file("favicon.svg", "image/svg+xml")
        if route == "/api/ping":
            # An open page says so once a minute; that is what keeps the idle
            # watchdog from shutting the server down.
            return self._json({"ok": True})
        if route == "/api/state":
            return self._json(state_payload())
        query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        ticker = str(query.get("ticker", "")).strip().upper()
        if route == "/api/company":
            return self._json({"ticker": ticker, "profile": store.company_profile(ticker)})
        if route == "/api/transcript":
            # Without a quarter: what can be offered. With one: the cached copy,
            # or null — reading never spends an API call; only the POST does.
            if query.get("quarter"):
                return self._json({"doc": transcripts.cached(ticker, query["quarter"].upper())})
            return self._json(transcripts.overview(ticker))
        self._json({"error": "not found"}, 404)

    def _same_origin_json(self) -> bool:
        """Could this POST only have come from the app's own page?

        Listening on 127.0.0.1 keeps other machines out, not other web pages:
        any site open in the same browser can send a form POST here and trade,
        overwrite the API key or spend its allowance. Two checks shut that:

        - Origin, when present, must be this server. Browsers attach it to
          every cross-site POST and a page cannot forge it. It may be absent
          for tools like curl, which are not the threat.
        - Content-Type must be JSON. A plain HTML form cannot send that, and a
          script on another site that tries needs a CORS preflight this server
          never answers — so the request never leaves the browser.

        app.js `api()` sends JSON from the same origin, so the page itself passes.
        """
        origin = self.headers.get("Origin")
        allowed = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
        if origin is not None and origin not in allowed:
            return False
        ctype = (self.headers.get("Content-Type") or "").strip().lower()
        return ctype.startswith("application/json")

    def do_POST(self) -> None:
        if not self._same_origin_json():
            return self._json({"error": "refused: requests must come from the app's own page"}, 403)
        self._touch()
        route = urlparse(self.path).path
        body = self._body()
        # Research is not the portfolio: fetching a transcript or saving a key
        # moves nothing the status export states, so these skip it.
        if route == "/api/transcript/fetch":
            try:
                doc = transcripts.fetch(str(body.get("ticker", "")), str(body.get("quarter", "")))
            except transcripts.TranscriptError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"doc": doc})
        if route == "/api/key":
            try:
                transcripts.save_key(str(body.get("key", "")))
            except transcripts.TranscriptError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"has_key": True})
        try:
            self._handle_post(route, body)
        finally:
            # Every POST here is a change to the portfolio — a trade, a split, a
            # struck row, a retyped target weight, a switch to another portfolio.
            # All of them move something the snapshot states, so all of them
            # refresh it. Hooking the routes one by one would work until the
            # ninth route was added and quietly did not.
            #
            # A rejected request lands here too and rewrites the same file with
            # the same content. That is the cheaper mistake: the alternative is
            # deciding per route what counts as a real change, and being wrong
            # about one of them.
            CACHE.export_snapshot("portfolio change", require_fresh=False)

    def _handle_post(self, route: str, body: dict) -> None:
        if route == "/api/prices/refresh":
            state = store.load()
            tickers = [h["ticker"] for h in store.holdings(store.active_portfolio(state))]
            CACHE.refresh(tickers)
            return self._json(state_payload())

        if route == "/api/portfolios":
            state = store.load()
            store.add_portfolio(state, str(body.get("name", "")))
            return self._json(state_payload())

        if route == "/api/portfolios/activate":
            state = store.load()
            if store.find(state, body.get("id")) is None:
                return self._json({"error": "no such portfolio"}, 404)
            state["active"] = body["id"]
            store.save(state)
            # Switching portfolios can bring in tickers never priced this run.
            missing = [h["ticker"] for h in store.holdings(store.active_portfolio(state))
                       if CACHE.price_for(h["ticker"]) is None]
            if missing:
                CACHE.refresh(missing)
            return self._json(state_payload())

        if route == "/api/portfolios/rename":
            state = store.load()
            pf = store.find(state, body.get("id"))
            if pf is None:
                return self._json({"error": "no such portfolio"}, 404)
            pf["name"] = str(body.get("name", "")).strip() or pf["name"]
            store.save(state)
            return self._json(state_payload())

        if route == "/api/portfolios/delete":
            state = store.load()
            if store.find(state, body.get("id")) is None:
                return self._json({"error": "no such portfolio"}, 404)
            try:
                store.delete_portfolio(state, body["id"])
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json(state_payload())

        if route in ("/api/buy", "/api/sell"):
            # Both are the same act now: append one row to the ledger. The
            # arithmetic that used to live here — weighted average on a buy,
            # realised profit on a sell — has moved into positions.fold, which
            # replays the whole history rather than nudging a stored figure.
            kind = "buy" if route.endswith("buy") else "sell"
            ticker = str(body.get("ticker", "")).strip().upper()
            qty = _num(body.get("shares"))
            price = _num(body.get("price"))
            if not ticker:
                return self._json({"error": "a ticker is required"}, 400)
            if qty <= 0:
                return self._json({"error": "quantity must be more than zero"}, 400)
            if price <= 0:
                return self._json({"error": "price must be more than zero"}, 400)

            state = store.load()
            pf = store.active_portfolio(state)
            if kind == "sell":
                fits, held = positions.can_sell(pf["transactions"], ticker, qty)
                if not fits:
                    return self._json(
                        {"error": f"you hold {held:.4f} {ticker} — cannot sell {qty:.4f}"}, 400)

            pf["transactions"].append(store.new_transaction({
                "date": _date(body.get("date")), "kind": kind, "ticker": ticker,
                "shares": qty, "price": price, "fee": _num(body.get("fee")),
                "note": str(body.get("note") or ""),
            }))
            entry = pf.setdefault("meta", {}).setdefault(ticker, {})
            entry["name"] = str(entry.get("name") or body.get("name") or ticker).strip()
            entry.setdefault("target", 0.0)
            store.save(state)
            if CACHE.price_for(ticker) is None:
                CACHE.refresh([ticker])
            return self._json(state_payload())

        if route == "/api/split":
            # No money moves — only the share count. Kept as its own kind so
            # the ledger can say what happened instead of faking a buy.
            ticker = str(body.get("ticker", "")).strip().upper()
            ratio = _num(body.get("ratio"))
            if not ticker:
                return self._json({"error": "a ticker is required"}, 400)
            if ratio <= 0:
                return self._json({"error": "a split ratio must be more than zero"}, 400)
            state = store.load()
            pf = store.active_portfolio(state)
            pf["transactions"].append(store.new_transaction({
                "date": _date(body.get("date")), "kind": "split",
                "ticker": ticker, "ratio": ratio, "note": str(body.get("note") or ""),
            }))
            store.save(state)
            return self._json(state_payload())

        if route == "/api/transactions/delete":
            # The undo the old design could not offer: strike the event and
            # every derived number follows, because none of them were stored.
            state = store.load()
            pf = store.active_portfolio(state)
            before = len(pf["transactions"])
            pf["transactions"] = [t for t in pf["transactions"] if t["id"] != body.get("id")]
            if len(pf["transactions"]) == before:
                return self._json({"error": "no such transaction"}, 404)
            store.save(state)
            return self._json(state_payload())

        if route == "/api/holdings":
            # Only policy is editable here now — a company's display name and
            # its target weight. Shares and average price are folded out of the
            # ledger, so typing over them would be typing over history; buying,
            # selling and splitting are how those move.
            state = store.load()
            pf = store.active_portfolio(state)
            was = pf.get("meta") or {}
            parked = {t for t, e in was.items() if isinstance((e or {}).get("bond"), dict)}
            meta = {}
            for row in body.get("holdings", []):
                ticker = str(row.get("ticker", "")).strip().upper()
                if not ticker:
                    continue
                # Keep anything the table cannot edit — a bond's emission terms
                # above all. A parked ticker also keeps target at 0: weights are
                # equity-only, and typing one in would invent a drift that lies.
                keep = {k: v for k, v in (was.get(ticker) or {}).items()
                        if k not in ("name", "target")}
                meta[ticker] = keep | {
                    "name": str(row.get("name") or ticker).strip(),
                    "target": 0.0 if ticker in parked else _num(row.get("target")),
                }
            # A company dropped from the table keeps its ledger rows: deleting
            # a row must not silently delete the trades behind it. If any
            # survive the fold, the company stays.
            held, _ = positions.fold(pf["transactions"])
            for ticker in held:
                meta.setdefault(ticker, was.get(ticker) or {"name": ticker, "target": 0.0})
            pf["meta"] = meta
            store.save(state)
            missing = [t for t in meta if CACHE.price_for(t) is None]
            if missing:
                CACHE.refresh(missing)
            return self._json(state_payload())

        self._json({"error": "not found"}, 404)

    def _file(self, name: str, ctype: str) -> None:
        target = STATIC / name
        if not target.exists():
            return self._json({"error": f"{name} missing"}, 404)
        self._send(target.read_bytes(), ctype)


def _date(value) -> str:
    """An ISO day, or nothing. A transaction with no date is honest about it;
    one stamped with today because the field was blank is not."""
    text = str(value or "").strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return text


def _num(value) -> float:
    try:
        return max(0.0, float(str(value).replace(",", ".").strip() or 0))
    except (TypeError, ValueError):
        return 0.0


def startup_fetch() -> None:
    """The automatic fetch: runs once, on open, before anyone clicks anything."""
    state = store.load()
    tickers = store.all_tickers(state)
    if not tickers:
        print("  startup fetch: no holdings yet — nothing to price")
        return
    snap = CACHE.refresh(tickers)
    print(f"  startup fetch: {snap['status']} — {'; '.join(snap['notes'])}")


def idle_watchdog() -> None:
    """Exit once nothing has asked for anything in IDLE_MINUTES."""
    if IDLE_MINUTES <= 0:
        return
    window = IDLE_MINUTES * 60
    while True:
        time.sleep(30)
        if time.monotonic() - LAST_REQUEST > window:
            # os._exit skips the buffer flush, so say it first
            print(f"  idle for {IDLE_MINUTES:g} min — shutting down", flush=True)
            cleanup_demo()      # os._exit skips atexit too
            os._exit(0)


def already_running() -> bool:
    """A second double-click should show the app, not crash on a taken port."""
    probe = socket.socket()
    probe.settimeout(0.4)
    try:
        probe.connect((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def enter_demo() -> None:
    """Run on a throwaway copy of the made-up portfolio in `demo/`.

    A copy, so clicking Buy in a demo never edits a file in the repository.
    Its own port, so a demo started next to the real app opens the demo and
    not your portfolio. No status export, so the real one in your notes is
    never overwritten with made-up numbers. Key and fetched transcripts keep
    living in the usual local folder: they are not portfolio data — unless
    that folder *is* `demo/`, in which case they go to the default `data/`,
    so a typed key never ends up beside the files that ship.

    The copy carries the DEMO marker along, which is what switches the export
    off. The copy is removed when the process ends (cleanup_demo), because the
    page promises that changes made in a demo vanish when the app stops.
    """
    global DEMO, PORT, DEMO_WORK
    local = store.local_dir()
    if local.resolve() == DEMO_SEED:
        local = store.DEFAULT_DATA_DIR
    os.environ["PORTFOLIO_LOCAL_DIR"] = str(local)
    work = Path(tempfile.mkdtemp(prefix="portfolio-demo-"))
    DEMO_WORK = work
    atexit.register(cleanup_demo)
    shutil.copytree(DEMO_SEED, work, dirs_exist_ok=True)
    os.environ["PORTFOLIO_DATA_DIR"] = str(work)
    os.environ["PORTFOLIO_NO_SNAPSHOT"] = "1"
    os.environ["PORTFOLIO_NO_APP_INSTALL"] = "1"
    DEMO, PORT = True, DEMO_PORT


def cleanup_demo() -> None:
    """Delete the temp copy enter_demo made. Only that directory, never the
    data folder in general: DEMO_WORK is set nowhere else. Safe to call twice."""
    global DEMO_WORK
    work, DEMO_WORK = DEMO_WORK, None
    if work is not None:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    global DEMO
    if "--demo" in sys.argv[1:]:
        enter_demo()
    elif store.data_dir().resolve() == DEMO_SEED:
        # PORTFOLIO_DATA_DIR=demo reaches the same made-up portfolio without
        # the flag. Working on it in place would edit tracked repository files
        # on the first click, so it gets the same throwaway copy.
        enter_demo()
        print("  data folder is the repository's demo/ — working on a temporary copy instead")
    DEMO = store.is_demo()
    url = f"http://{HOST}:{PORT}/"
    if already_running():
        print(f"Already running — opening {url}")
        webbrowser.open(url)
        return

    print(f"Investment Portfolio{' — DEMO, made-up data' if DEMO else ''} — {url}")
    print(f"  data file: {store.path()}")
    print(f"  stops by itself {IDLE_MINUTES:g} min after the last page closes"
          if IDLE_MINUTES > 0 else "  runs until stopped")
    launcher.ensure()
    threading.Thread(target=startup_fetch, daemon=True).start()
    threading.Thread(target=idle_watchdog, daemon=True).start()
    if not os.environ.get("PORTFOLIO_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
