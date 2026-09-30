"""Earnings-call transcripts, fetched on request and kept on this machine.

Source: Alpha Vantage's EARNINGS_CALL_TRANSCRIPT, which needs a free API key
and a quarter (`2026Q2`). There is no "latest" — asking for a quarter that has
not been published returns nothing and still spends one call of a small daily
allowance. So nothing here guesses: one click, one quarter, one call, and the
answer is cached so the same quarter is never asked for twice.

What leaves the machine is a ticker, a quarter and the key. The transcript
comes back as turns — speaker, title, what they said — and is written to
`transcripts/` in the local folder, which is never shipped with the app.

The key lives in `secrets.json` beside it, readable only by this user. It is
never sent to the page: the page learns whether a key exists, not what it is.

A quarter that came back empty is written down too, in `transcripts/_missed.json`,
so the page can preselect a quarter that is likely to exist instead of spending
the allowance probing the same empty one every day. Transcripts do get published
late, so a miss is forgotten after MISS_TTL_DAYS.

Every ticker and quarter that arrives here came from a URL or a request body and
ends up in a file name, so each public function checks both against a pattern
before a path is built: `../../x` is not a ticker, and must not become a file.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import store

AV_URL = "https://www.alphavantage.co/query"
SECRETS = "secrets.json"
MISSED = "_missed.json"         # leading underscore: no ticker can start with one
MISS_TTL_DAYS = 14

# Alpha Vantage's own quarter format, and the only shape allowed into a file name.
QUARTER = re.compile(r"\d{4}Q[1-4]")
# Keys are letters and digits. The length bound is loose on purpose: this stops
# junk being saved, it does not second-guess the provider's format.
KEY = re.compile(r"[A-Za-z0-9]{1,64}")

_missed_lock = threading.Lock()     # two fetches at once must not lose each other's miss


class TranscriptError(Exception):
    """Why a transcript is not here, worded for the person who asked."""


# ---- key ------------------------------------------------------------------
def _secrets_path() -> Path:
    return store.local_dir() / SECRETS


def api_key() -> str:
    try:
        return str(json.loads(_secrets_path().read_text("utf-8")).get("alphavantage") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def has_key() -> bool:
    return bool(api_key())


def save_key(key: str) -> None:
    """Store the key, readable only by this user from the moment the file exists.

    Writing first and chmod-ing after leaves a window in which the key sits in
    a world-readable file; os.open with a mode closes it for a new file. That
    mode is ignored when the file already exists, hence the chmod as well.
    """
    key = str(key or "").strip()
    if not KEY.fullmatch(key):
        raise TranscriptError("that does not look like an Alpha Vantage key")
    p = _secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current["alphavantage"] = key
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(current, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass            # Windows: permissions work differently; the file still stays local


# ---- quarters -------------------------------------------------------------
def recent_quarters(n: int = 4, today: date | None = None) -> list[str]:
    """Newest first, starting with the last *finished* calendar quarter."""
    d = today or date.today()
    year, q = d.year, (d.month - 1) // 3        # current quarter index 0..3 → last finished is q
    if q == 0:
        year, q = year - 1, 4
    out = []
    for _ in range(n):
        out.append(f"{year}Q{q}")
        q -= 1
        if q == 0:
            year, q = year - 1, 4
    return out


# ---- cache ----------------------------------------------------------------
def _cache_dir() -> Path:
    return store.local_dir() / "transcripts"


def _clean(ticker: str, quarter: str = "2000Q1") -> tuple[str, str] | None:
    """Ticker and quarter as they may appear in a file name, or None.

    The one gate between request input and a path. The default quarter lets
    ticker-only callers go through the same gate instead of a second, weaker one.
    """
    ticker = str(ticker or "").strip().upper()
    quarter = str(quarter or "").strip().upper()
    if store.TICKER.fullmatch(ticker) and QUARTER.fullmatch(quarter):
        return ticker, quarter
    return None


def _cache_file(ticker: str, quarter: str) -> Path:
    if _clean(ticker, quarter) != (ticker, quarter):
        raise TranscriptError("ticker or quarter not recognised")
    return _cache_dir() / f"{ticker}-{quarter}.json"


def cached_quarters(ticker: str) -> list[str]:
    """Quarters already on disk for this ticker, newest first."""
    ok = _clean(ticker)
    d = _cache_dir()
    if not ok or not d.is_dir():
        return []
    prefix = f"{ok[0]}-"
    # The glob alone would also take `BRK-B-2025Q1` for ticker `BRK`; the
    # quarter pattern on what is left is what makes the match exact.
    return sorted((q for q in (p.stem[len(prefix):] for p in d.glob(f"{prefix}*.json"))
                   if QUARTER.fullmatch(q)), reverse=True)


def cached(ticker: str, quarter: str) -> dict | None:
    """The stored transcript, or None. Input that is not a ticker and a quarter
    also gets None — which is all a path-traversal attempt ever sees."""
    ok = _clean(ticker, quarter)
    if not ok:
        return None
    try:
        return json.loads(_cache_file(*ok).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---- misses ---------------------------------------------------------------
def _missed_file() -> Path:
    return _cache_dir() / MISSED


def _read_missed() -> dict:
    try:
        data = json.loads(_missed_file().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _note_miss(ticker: str, quarter: str, found: bool) -> None:
    """Write down that a quarter came back empty today — or forget it once found.

    Best effort: a note that could not be saved costs one repeated probe later,
    which is no reason to fail the request that is being answered now.
    """
    with _missed_lock:
        data = _read_missed()
        per = data.get(ticker) if isinstance(data.get(ticker), dict) else {}
        if found:
            if quarter not in per:
                return
            per.pop(quarter)
        else:
            per[quarter] = date.today().isoformat()
        if per:
            data[ticker] = per
        else:
            data.pop(ticker, None)
        try:
            target = _missed_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
        except OSError:
            pass


def missed_quarters(ticker: str, today: date | None = None) -> dict[str, str]:
    """Quarters that came back empty recently: {quarter: date checked}.

    Older misses are dropped rather than trusted: a transcript that was not
    there the week after the call is often there a month later.
    """
    ok = _clean(ticker)
    if not ok:
        return {}
    per = _read_missed().get(ok[0])
    if not isinstance(per, dict):
        return {}
    cutoff = (today or date.today()) - timedelta(days=MISS_TTL_DAYS)
    out = {}
    for quarter, checked in per.items():
        try:
            when = date.fromisoformat(str(checked))
        except ValueError:
            continue
        if QUARTER.fullmatch(str(quarter)) and when >= cutoff:
            out[quarter] = when.isoformat()
    return out


# ---- fetch ----------------------------------------------------------------
def fetch(ticker: str, quarter: str) -> dict:
    """The transcript for one quarter: from the cache if it was fetched before,
    otherwise from Alpha Vantage, then cached.

    `from_cache` on the result says which — True means no API call was spent.
    """
    ok = _clean(ticker, quarter)
    if not ok:
        raise TranscriptError("ticker or quarter not recognised")
    ticker, quarter = ok
    hit = cached(ticker, quarter)
    if hit:
        return hit | {"from_cache": True}

    key = api_key()
    if not key:
        raise TranscriptError("no API key yet — add a free Alpha Vantage key first")
    query = urllib.parse.urlencode({"function": "EARNINGS_CALL_TRANSCRIPT",
                                    "symbol": ticker, "quarter": quarter, "apikey": key})
    try:
        with urllib.request.urlopen(AV_URL + "?" + query, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TranscriptError(f"could not reach Alpha Vantage ({type(exc).__name__})") from exc
    if not isinstance(payload, dict):
        raise TranscriptError("Alpha Vantage sent something that is not a transcript")

    turns = payload.get("transcript") or []
    if not turns:
        # Rate limits and plan limits arrive as a 200 with a message instead of
        # data. Those say nothing about the quarter, so only a reply with no
        # message at all is written down as a miss — recording a rate limit
        # would steer the page away from a quarter that does exist.
        said = payload.get("Information") or payload.get("Note") or payload.get("Error Message")
        if not said:
            _note_miss(ticker, quarter, found=False)
        raise TranscriptError(said or f"no transcript for {ticker} {quarter} — try an earlier quarter")

    doc = {
        "ticker": ticker,
        "quarter": quarter,
        "source": "Alpha Vantage",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "turns": [{"speaker": str(t.get("speaker") or ""),
                   "title": str(t.get("title") or ""),
                   "content": str(t.get("content") or "")} for t in turns],
    }
    target = _cache_file(ticker, quarter)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
    _note_miss(ticker, quarter, found=True)
    return doc | {"from_cache": False}


def overview(ticker: str) -> dict:
    """What the page needs before anything is fetched.

    `cached`   quarters on disk, newest first.
    `missed`   {quarter: date checked} that came back empty in the last
               MISS_TTL_DAYS.
    `quarters` the last four finished quarters plus anything cached, newest
               first, no repeats.
    `default`  the quarter to preselect: the newest one already here (free to
               open), else the newest not recently missed (most likely to
               exist), else simply the newest. Picking it well is what keeps a
               click from being a wasted call of the daily allowance.
    """
    ticker = str(ticker or "").strip().upper()
    have = cached_quarters(ticker)
    missed = missed_quarters(ticker)
    offer = sorted(set(recent_quarters()) | set(have), reverse=True)
    untried = [q for q in offer if q not in missed]
    default = have[0] if have else (untried[0] if untried else offer[0])
    return {"ticker": ticker, "has_key": has_key(), "cached": have, "missed": missed,
            "quarters": offer, "default": default}
