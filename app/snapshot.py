"""The portfolio written out as one Markdown status file.

`data/portfolios.json` is a transaction ledger. Reading it means knowing that
positions are folded out of it, that a sale does not move the average price,
and that weights depend on live prices. This exports the *result* instead: one
row per company with shares, average price, current price, value, weight
against target, and drift. Facts only — no verdicts, no suggested weights.

The file is written read-only and says its own age in the frontmatter, because
the two ways a status file misleads are being edited by hand and being older
than it looks.

`export()` is the whole surface. server.py calls it after every successful
fetch; scripts/portfolio-snapshot.py calls it from the command line.
"""

from __future__ import annotations

import os
import stat
from datetime import datetime
from pathlib import Path

import store

# Default destination in the user's Documents folder. PORTFOLIO_SNAPSHOT_FILE
# redirects it — a notes vault, another folder, or a dry run without writing
# into the real one. If the folder does not exist, export() quietly does nothing.
DEFAULT_OUT = Path(
    os.environ.get("PORTFOLIO_SNAPSHOT_FILE")
    or Path.home() / "Documents" / "portfolio-status.md"
).expanduser()


def money(value: float) -> str:
    return f"${value:,.2f}"


def build(portfolio: dict, quotes: dict) -> tuple[list[dict], dict]:
    """One row per holding, plus totals. Weights are equity-only.

    A parked bond (emission terms in meta) adds to total wealth but is left out
    of the weight denominator. Mixing it in would make every equity look
    underweight by the bond's share of wealth — the wrong signal for rebalance
    and for anyone reading the percentages.
    """
    parked = {
        t for t, e in (portfolio.get("meta") or {}).items()
        if isinstance((e or {}).get("bond"), dict)
    }
    rows = []
    for holding in store.holdings(portfolio):
        quote = quotes.get(holding["ticker"]) or {}
        price = quote.get("price")
        value = (price or 0) * holding["shares"] if price is not None else 0.0
        is_parked = holding["ticker"] in parked
        rows.append(holding | {
            "parked": is_parked,
            "target": 0.0 if is_parked else holding["target"],
            "price": price,
            "source": quote.get("source"),
            "as_of": quote.get("as_of"),
            "as_of_kind": quote.get("as_of_kind"),
            "value": value if price is not None else 0.0,
            "unrealised": (value - holding["cost"]) if price is not None else None,
        })

    equities = [r for r in rows if not r["parked"]]
    bonds = [r for r in rows if r["parked"]]
    equity_total = sum(r["value"] for r in equities if r["price"] is not None)
    bond_total = sum(r["value"] for r in bonds if r["price"] is not None)
    invested = sum(r["cost"] for r in rows)

    for r in equities:
        if r["price"] is None:
            r["weight"] = r["drift"] = None
        else:
            r["weight"] = (r["value"] / equity_total * 100) if equity_total else 0.0
            r["drift"] = r["weight"] - r["target"]
    for r in bonds:
        r["weight"] = r["drift"] = None

    equities.sort(key=lambda r: abs(r["drift"] or 0), reverse=True)
    rows = equities + bonds
    return rows, {
        "total": equity_total + bond_total,
        "equity_total": equity_total,
        "bond_total": bond_total,
        "invested": invested,
        "unrealised": equity_total + bond_total - invested,
        "realised": store.realised(portfolio),
        "targets": sum(r["target"] for r in equities),
    }


def render(portfolio: dict, rows: list[dict], totals: dict, price_note: str) -> str:
    now = datetime.now().astimezone()
    equities = [r for r in rows if not r["parked"]]
    bonds = [r for r in rows if r["parked"]]
    stamps = sorted({r["as_of"] for r in equities if r.get("as_of")})
    unpriced = [r["ticker"] for r in rows if not r.get("price")]

    head = [
        "---",
        f'snapshot_at: "{now.isoformat(timespec="seconds")}"',
        f'portfolio: "{portfolio["name"]}"',
        f"position_count: {len(rows)}",
        f"value_usd: {totals['total']:.2f}",
        f"equities_usd: {totals['equity_total']:.2f}",
        f"bonds_usd: {totals['bond_total']:.2f}",
        f"invested_usd: {totals['invested']:.2f}",
        f"unrealised_pnl_usd: {totals['unrealised']:.2f}",
        f"realised_pnl_usd: {totals['realised']:.2f}",
        f"target_weight_sum_pct: {totals['targets']:.1f}",
        f'price_source: "{price_note}"',
        f'prices_as_of: "{stamps[-1] if stamps else "none"}"',
        "type: portfolio-snapshot",
        "---",
        "",
        f"# Portfolio status — {now:%Y-%m-%d %H:%M}",
        "",
        "## Notes",
        "",
        "Written by the local portfolio app in this checkout. The app holds the",
        "transaction ledger and computes positions and weights — this file is only",
        "the result.",
        "",
        "- **Numbers below are already computed** from the ledger and current quotes.",
        "- **Weights are equities only.** Savings bonds (if any) add to wealth but stay",
        "  out of the weight denominator and have no rebalance target.",
        "- **`Drift`:** positive = over target, negative = under.",
        "- **Auto-refreshed** on every price fetch and after every portfolio change.",
        "- **Read-only and fully overwritten** on each export — keep notes elsewhere.",
        "- **Stale `snapshot_at`?** Open the app to refresh.",
        "",
    ]
    if totals["invested"]:
        wealth = (
            f"Wealth **{money(totals['total'])}** "
            f"(equities {money(totals['equity_total'])}"
            + (f" · bonds {money(totals['bond_total'])}" if totals["bond_total"] else "")
            + f") · invested {money(totals['invested'])} · "
            f"unrealised **{totals['unrealised']:+,.2f} USD** "
            f"({totals['unrealised'] / totals['invested'] * 100:+.1f}%)"
        )
    else:
        wealth = "No positions."
    head += [
        wealth,
        f"Realised (closed sales, lifetime): **{totals['realised']:+,.2f} USD**",
        "",
        f"Prices: {price_note}, stamp {stamps[-1] if stamps else 'none'}.",
        "",
        "## Equities (weights among themselves = 100%)",
        "",
        "Sorted by absolute drift from target. Weight = share of **equity** value, not total wealth.",
        "",
        "| Ticker | Company | Shares | Avg price | Quote | Value | Weight | Target | Drift | Unreal. |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]

    body = []
    for r in equities:
        price = money(r["price"]) if r["price"] else "—"
        unreal = f"{r['unrealised']:+,.2f}" if r["unrealised"] is not None else "—"
        w = f"{r['weight']:.2f}%" if r["weight"] is not None else "—"
        d = f"{r['drift']:+.2f} pp" if r["drift"] is not None else "—"
        body.append(
            f"| {r['ticker']} | {r['name']} | {r['shares']:.4f} | {money(r['avg_price'])} | "
            f"{price} | {money(r['value'])} | {w} | {r['target']:.2f}% | "
            f"{d} | {unreal} |"
        )

    mid = []
    if bonds:
        mid = [
            "",
            "## Bonds (outside weights)",
            "",
            "They park capital. They count toward wealth, **not** allocation. No target,",
            "no drift, no rebalance. Value = face + accrued interest, gross, at NBP mid rate.",
            "",
            "| Ticker | Name | Units | Avg price | Quote | Value | Unreal. |",
            "|---|---|--:|--:|--:|--:|--:|",
        ]
        for r in bonds:
            price = money(r["price"]) if r["price"] else "—"
            unreal = f"{r['unrealised']:+,.2f}" if r["unrealised"] is not None else "—"
            mid.append(
                f"| {r['ticker']} | {r['name']} | {r['shares']:.4f} | {money(r['avg_price'])} | "
                f"{price} | {money(r['value'])} | {unreal} |"
            )

    tail = ["", "## Notes on reading these numbers", ""]
    if abs(totals["targets"] - 100) > 0.05:
        tail.append(f"- Equity target weights sum to **{totals['targets']:.1f}%**, not 100%. "
                    "The app does not normalise them — it shows what was entered.")
    if unpriced:
        tail.append(f"- No quote, valued at 0: **{', '.join(unpriced)}**. "
                    "Other equity weights are inflated as a result.")
    if len(stamps) > 1:
        tail.append(f"- Quotes carry {len(stamps)} different timestamps "
                    f"({stamps[0]} … {stamps[-1]}) — the source stamps each symbol separately.")
    if bonds:
        tail.append(
            "- The bond section is **separate from allocation**. Do not fold them into "
            "equity percentages and do not propose selling them in a rebalance."
        )
    tail += [
        "- **Avg price excludes commission** — it lines up with the broker's share price.",
        "- A sale does not move the average price of remaining shares; sale P&L lives in "
        "realised and never changes again.",
        "- Positions are recomputed from transactions on every export, not stored.",
        f"- Source of truth: `{store.path()}` ({len(portfolio['transactions'])} transactions).",
    ]
    return "\n".join(head + body + mid + tail) + "\n"



def _unlock(out: Path) -> None:
    """Undo whatever the previous export locked, so this one can write."""
    if hasattr(os, "chflags"):
        try:
            # Clear only the immutable bit; any other flag on the file is
            # somebody else's business.
            os.chflags(out, out.stat().st_flags & ~stat.UF_IMMUTABLE)
        except (OSError, AttributeError):
            pass
    out.chmod(0o644)


def write_read_only(out: Path, text: str) -> None:
    """Write the snapshot, then make it as hard to edit as the OS allows.

    A polite "please do not edit" in the body is not a guarantee. What this must
    refuse is the *silent* edit: notes written on top of numbers that the next
    export then quietly replaces.

    Read-only mode alone does not achieve that, and it is worth writing down
    why, because it looks like it does. `chmod 0o444` stops a write *into* the
    file — a shell redirect, `write_text`, most editors' save. It does not stop
    a write-then-rename: a new file beside this one, renamed over it. That is
    governed by the directory's permissions, not the file's, and it is how
    `sed -i` works, and several editors besides. Measured, not assumed: against
    a 0o444 file, `sed -i` rewrote it and reported no error at all.

    So on macOS and the BSDs the immutable flag goes on as well, and that is
    what actually refuses the rename (`sed: rename(): Operation not permitted`).
    Elsewhere — Linux, where the equivalent needs root — the mode bits are all
    there is, and this function is honest about being weaker there.

    Still a guard rail, not a lock: `chflags nouchg <plik>` takes it off, which
    is exactly what a person who means to edit should have to type.
    """
    if out.exists():
        _unlock(out)
    out.write_text(text, "utf-8")
    out.chmod(0o444)
    if hasattr(os, "chflags"):
        try:
            os.chflags(out, out.stat().st_flags | stat.UF_IMMUTABLE)
        except OSError:
            # A filesystem that has no immutable flag (a network or FAT volume,
            # some sync folders) is not a reason to fail the export — the file
            # is written and still read-only. Losing the stronger guard is
            # worth less than losing the snapshot.
            pass


def export(quotes: dict, price_note: str, out: Path | None = None,
           create_dir: bool = False) -> Path | None:
    """Write the active portfolio to `out`. Returns the path written, or None
    when nothing was written.

    Nothing is written for a demo data folder (store.is_demo): made-up numbers
    must never land in a real status file, however the app was started.

    DEFAULT_OUT is one person's notes folder. When `out` is that default and
    its folder does not exist, this returns None without writing and without
    creating anything — `create_dir` included — so a fresh clone on someone
    else's machine never grows folders in their Documents. Only an explicit
    destination (scripts/portfolio-snapshot.py -o) may have its folder created.

    For an explicit destination `create_dir` stays False on the automatic path:
    if the folder is not there — renamed, on a drive that has not mounted,
    mid-sync — the honest outcome is a refusal the caller can report. Creating
    it would leave a lone Markdown file in a directory that means nothing, and
    the status file would look current when it is not.
    """
    if store.is_demo():
        return None
    out = out or DEFAULT_OUT
    if out == DEFAULT_OUT and not out.parent.is_dir():
        return None
    if create_dir:
        out.parent.mkdir(parents=True, exist_ok=True)
    elif not out.parent.is_dir():
        raise FileNotFoundError(f"brak katalogu docelowego: {out.parent}")

    state = store.load()
    portfolio = store.active_portfolio(state)
    rows, totals = build(portfolio, quotes)
    write_read_only(out, render(portfolio, rows, totals, price_note))
    return out
