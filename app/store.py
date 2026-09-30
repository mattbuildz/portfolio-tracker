"""Where the portfolios live on disk.

One JSON file in `data/` next to this checkout. It sat in the platform's
application-support directory first, which is the conventional place for it and
keeps data alive when a checkout is deleted — but it put the file two folders
deep in a Library path nobody browses, and editing or reading the portfolio by
hand meant pasting that path every time. Keeping it beside the code it belongs
to won the trade; `.gitignore` is what keeps it out of commits now.

The directory is resolved from this file's own location, not the working
directory, so it does not matter whether the server was started from a terminal,
the desktop launcher or an editor. Two environment variables move things:

  PORTFOLIO_DATA_DIR   the folder holding portfolios.json and companies/.
  PORTFOLIO_LOCAL_DIR  machine-local files that are not the portfolio — the
                       API key and fetched transcripts. Defaults to the data
                       folder; demo mode points it back at the real one.

A data folder containing a file named `DEMO` holds a made-up portfolio (the
repository's `demo/` ships with one). `is_demo()` is the single test the rest
of the app asks, so the demo stays harmless however it was started.

What is stored is the **transaction ledger** — buys, sells and splits — plus
the policy that cannot be derived from it: each company's target weight and
display name. Positions are not stored. They are folded out of the ledger by
app/positions.py every time they are asked for, exactly as CONTEXT.md says.

A savings bond's emission terms also live in `meta` under a `bond` key. They
are not a transaction and not derivable from one. A ticker with that key is
priced from the terms (app/bonds.py) and kept **outside** equity weights.

Storing the state instead of the events was the original shape of this file,
and it lost things quietly: no history, no way to split realised profit across
tax years, no undo, nowhere to record a share split.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path

import positions as position_fold

SCHEMA = 2

# What a ticker may look like when it becomes part of a file name.
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,9}")


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Presence of this file in the data folder marks the portfolio as made up.
DEMO_MARKER = "DEMO"


def data_dir() -> Path:
    """PORTFOLIO_DATA_DIR if set, otherwise `data/` beside this checkout."""
    override = os.environ.get("PORTFOLIO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return DEFAULT_DATA_DIR


def local_dir() -> Path:
    """Machine-local files that are not the portfolio: the API key, fetched
    transcripts. PORTFOLIO_LOCAL_DIR if set, otherwise the same folder as the
    ledger. Demo mode sets it, because there the ledger is a throwaway copy and
    these stay where they were — so a transcript fetched once serves both, and
    a key typed in the demo is not lost with it."""
    override = os.environ.get("PORTFOLIO_LOCAL_DIR")
    return Path(override).expanduser() if override else data_dir()


def is_demo() -> bool:
    """True when the data folder holds a made-up portfolio.

    Decided by a file in the folder, not by how the server was started: a
    `--demo` flag can be forgotten, a marker that travels with the data cannot.
    The status export and anything else that would carry demo numbers out of
    the app ask this.
    """
    return (data_dir() / DEMO_MARKER).is_file()


def companies_dir() -> Path:
    """Company profiles, one `<TICKER>.json` each. Optional: most tickers have none."""
    return data_dir() / "companies"


def company_profile(ticker: str) -> dict | None:
    if not TICKER.fullmatch(ticker.upper()):
        return None
    target = companies_dir() / f"{ticker.upper()}.json"
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def profiled_tickers() -> list[str]:
    d = companies_dir()
    return sorted(p.stem.upper() for p in d.glob("*.json")) if d.is_dir() else []


DATA_FILE = "portfolios.json"

KINDS = ("buy", "sell", "split")


def _blank_portfolio(name: str = "Main Portfolio") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name,
            "transactions": [], "meta": {}, "realised_opening": 0.0}


def _default_state() -> dict:
    first = _blank_portfolio()
    return {"version": SCHEMA, "active": first["id"], "portfolios": [first]}


def path() -> Path:
    return data_dir() / DATA_FILE


def new_transaction(fields: dict) -> dict:
    """One ledger row, with everything its kind needs and nothing it does not."""
    kind = str(fields.get("kind", "buy")).strip().lower()
    if kind not in KINDS:
        kind = "buy"
    tx = {
        "id": uuid.uuid4().hex[:12],
        "date": str(fields.get("date") or "").strip()[:10],
        "kind": kind,
        "ticker": str(fields.get("ticker", "")).strip().upper(),
        "note": str(fields.get("note") or "").strip(),
    }
    if kind == "split":
        tx["ratio"] = float(fields.get("ratio") or 0)
    else:
        tx["shares"] = float(fields.get("shares") or 0)
        tx["price"] = float(fields.get("price") or 0)
        tx["fee"] = float(fields.get("fee") or 0)
    return tx


def load() -> dict:
    p = path()
    if not p.exists():
        state = _default_state()
        save(state)
        return state
    try:
        state = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt file is kept, not overwritten — losing hand-typed holdings
        # silently would be the worst thing this app could do.
        backup = p.with_suffix(".corrupt.json")
        p.replace(backup)
        state = _default_state()
        state["note"] = f"previous file could not be read; kept at {backup}"
        save(state)

    was = int(state.get("version") or 1)
    state = _normalise(state)
    if was < SCHEMA:
        # Migration rewrites the file, so keep what it was before it does.
        legacy = p.with_suffix(f".v{was}.json")
        if p.exists() and not legacy.exists():
            legacy.write_bytes(p.read_bytes())
        save(state)
    return state


def _migrate_v1(pf: dict) -> None:
    """Holdings-as-state become the transactions that would have produced them.

    A v1 file recorded only where the portfolio had arrived: shares, average
    price, a realised total. The events behind them are gone for good, so each
    holding becomes one opening buy with no date — honest about being a
    starting balance rather than inventing a day it did not happen on.

    The old realised total cannot be turned back into sales either. It is kept
    verbatim in its own field and added to whatever later sales realise;
    CONTEXT.md says a realised figure never changes again, and dropping it in
    the name of a tidy schema would change it to zero.
    """
    meta = pf.setdefault("meta", {})
    opening = []
    for h in pf.get("holdings") or []:
        ticker = str(h.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        entry = meta.setdefault(ticker, {})
        entry.setdefault("name", str(h.get("name") or ticker))
        entry.setdefault("target", float(h.get("target") or 0))
        shares = float(h.get("shares") or 0)
        if shares > 0:
            opening.append(new_transaction({
                "date": "", "kind": "buy", "ticker": ticker, "shares": shares,
                "price": float(h.get("avg_price") or 0), "fee": 0.0,
                "note": "opening balance",
            }))
    pf["transactions"] = opening + list(pf.get("transactions") or [])
    pf["realised_opening"] = float(pf.get("realised") or 0)
    pf.pop("holdings", None)
    pf.pop("realised", None)


def _normalise(state: dict) -> dict:
    portfolios = state.get("portfolios") or []
    if not portfolios:
        portfolios = [_blank_portfolio()]
    for pf in portfolios:
        pf.setdefault("id", uuid.uuid4().hex[:8])
        pf.setdefault("name", "Portfolio")
        if "holdings" in pf or "transactions" not in pf:
            _migrate_v1(pf)
        pf["transactions"] = [new_transaction(t) | {"id": t.get("id") or uuid.uuid4().hex[:12]}
                              for t in pf.get("transactions") or []]
        pf["realised_opening"] = float(pf.get("realised_opening") or 0)
        meta = pf.setdefault("meta", {})
        for ticker in {t["ticker"] for t in pf["transactions"] if t["ticker"]}:
            entry = meta.setdefault(ticker, {})
            entry["name"] = str(entry.get("name") or ticker)
            entry["target"] = float(entry.get("target") or 0)
    state["portfolios"] = portfolios
    ids = {pf["id"] for pf in portfolios}
    if state.get("active") not in ids:
        state["active"] = portfolios[0]["id"]
    state["version"] = SCHEMA
    return state


def save(state: dict) -> None:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    # Write-then-rename: a crash mid-save leaves the old file intact.
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, d / DATA_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def active_portfolio(state: dict) -> dict:
    return next(pf for pf in state["portfolios"] if pf["id"] == state["active"])


def find(state: dict, portfolio_id: str) -> dict | None:
    return next((pf for pf in state["portfolios"] if pf["id"] == portfolio_id), None)


def holdings(pf: dict) -> list[dict]:
    """The portfolio as rows on screen: one per company still held.

    Everything here is derived. A company with a target weight but no shares —
    sold out of, or set up before the first purchase — still appears, because
    its target is policy the user wrote down and a rebalance has to see it.
    """
    folded, _ = position_fold.fold(pf["transactions"])
    meta = pf.get("meta") or {}
    rows = []
    for ticker in sorted(set(folded) | {t for t, m in meta.items() if float(m.get("target") or 0) > 0}):
        pos = folded.get(ticker) or {"shares": 0.0, "avg_price": 0.0, "cost": 0.0, "fees": 0.0}
        entry = meta.get(ticker) or {}
        rows.append({
            "ticker": ticker,
            "name": str(entry.get("name") or ticker),
            "target": float(entry.get("target") or 0),
            "shares": pos["shares"],
            "avg_price": pos["avg_price"],
            "cost": pos["cost"],
            "fees": pos["fees"],
        })
    return rows


def realised(pf: dict) -> float:
    _, sold = position_fold.fold(pf["transactions"])
    return sold + float(pf.get("realised_opening") or 0)


def add_portfolio(state: dict, name: str) -> dict:
    pf = _blank_portfolio(name.strip() or "Portfolio")
    state["portfolios"].append(pf)
    state["active"] = pf["id"]
    save(state)
    return pf


def delete_portfolio(state: dict, portfolio_id: str) -> None:
    if len(state["portfolios"]) <= 1:
        raise ValueError("the last portfolio cannot be deleted")
    state["portfolios"] = [pf for pf in state["portfolios"] if pf["id"] != portfolio_id]
    if state["active"] == portfolio_id:
        state["active"] = state["portfolios"][0]["id"]
    save(state)


def bond_specs(state: dict) -> dict[str, dict]:
    """Tickers priced from stored emission terms, not from a market.

    Across all portfolios (prices are cached by ticker). First definition wins.
    """
    specs: dict[str, dict] = {}
    for pf in state["portfolios"]:
        for ticker, entry in (pf.get("meta") or {}).items():
            spec = (entry or {}).get("bond")
            if isinstance(spec, dict) and ticker not in specs:
                specs[ticker] = spec
    return specs


def all_tickers(state: dict) -> list[str]:
    seen: list[str] = []
    for pf in state["portfolios"]:
        for row in holdings(pf):
            if row["ticker"] and row["ticker"] not in seen:
                seen.append(row["ticker"])
    return seen
