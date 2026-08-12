"""Foreslår varetype for produkter som verken har eksplisitt mapping eller
treffer keyword-reglene i product_types.py — altså de som i dag ender i grov
kategori-fallback («meieri-annet» osv.) og dermed aldri får egen kadens.

Bruker LLM-en (jf. llm.py) med de eksisterende varetypene som vokabular, og
skriver ev. inn i data/product_types.json (som er versjonert — se over
git-diffen før commit).

Bruk:
    uv run python -m min_oda.klassifiser           # forhåndsvisning
    uv run python -m min_oda.klassifiser --apply   # skriv til product_types.json
"""

from __future__ import annotations

import argparse
import json

import pandas as pd
from rich.console import Console
from rich.table import Table

from . import llm
from .data_loader import load_both
from .product_types import (
    _CATEGORY_FALLBACK,
    _explicit_mapping,
    _KEYWORD_RULES,
    MAPPING_FILE,
)

console = Console()

_BATCH = 40


def finn_kandidater(lines: pd.DataFrame) -> list[dict]:
    """Distinkte produkter der klassifiseringen faller gjennom til
    kategori-fallback (eller ingenting): ingen eksplisitt mapping på
    produkt-id, ingen keyword-regel som treffer navnet."""
    mapping = _explicit_mapping()
    df = (
        lines.dropna(subset=["product_id", "product_name"])
        .drop_duplicates("product_id")
    )
    out: list[dict] = []
    for _, r in df.iterrows():
        pid = int(r["product_id"])
        if pid in mapping:
            continue
        low = str(r["product_name"]).lower()
        if any(p.search(low) for p, _ in _KEYWORD_RULES):
            continue
        out.append({
            "product_id": pid,
            "name": str(r["product_name"]),
            "category": str(r.get("category") or ""),
        })
    return out


def _vokabular() -> list[str]:
    typer = {t for _, t in _KEYWORD_RULES}
    typer.update(_CATEGORY_FALLBACK.values())
    typer.update(_explicit_mapping().values())
    return sorted(typer)


def foreslaa_typer(kandidater: list[dict], chat=None) -> dict[int, str]:
    """LLM-forslag {produkt-id: varetype} for kandidatene. Batches for å
    holde promptene håndterbare."""
    chat = chat or llm.chat
    vokab = ", ".join(_vokabular())
    system = (
        "Du klassifiserer dagligvarer til en varetype — en kort norsk nøkkel "
        "med små bokstaver og bindestrek, slik at byttbare merkevarer av samme "
        "behov får samme nøkkel. Bruk en eksisterende varetype når den passer; "
        "lag ellers en ny i samme stil. Svar null for produkter uten naturlig "
        "varetype (gavekort, sesongting uten gjenkjøpsbehov)."
    )
    forslag: dict[int, str] = {}
    for i in range(0, len(kandidater), _BATCH):
        batch = kandidater[i : i + _BATCH]
        listing = "\n".join(
            f"[{k['product_id']}] {k['name']} (kategori: {k['category']})"
            for k in batch
        )
        user = (
            f"Eksisterende varetyper: {vokab}\n\n"
            f"Produkter:\n{listing}\n\n"
            'Svar KUN med JSON: {"<produkt-id>": "<varetype eller null>"}'
        )
        data = llm.extract_json(chat(system, user, max_tokens=1500))
        if not isinstance(data, dict):
            console.print("[yellow]Uparsebart svar for en batch — hopper over.[/yellow]")
            continue
        for pid_raw, t in data.items():
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError):
                continue
            if t and isinstance(t, str) and any(k["product_id"] == pid for k in batch):
                forslag[pid] = t.strip().lower()
    return forslag


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Skriv forslagene inn i data/product_types.json")
    args = p.parse_args()

    if not llm.enabled():
        console.print("[red]Ingen LLM-provider tilgjengelig[/red] "
                      "(jf. LLM_PROVIDER i .env).")
        return

    _, lines = load_both()
    kandidater = finn_kandidater(lines)
    if not kandidater:
        console.print("Alle produkter er allerede dekket av regler eller mapping.")
        return
    console.print(f"{len(kandidater)} produkter uten regel-treff, "
                  f"spør {llm.provider_label()} …")

    forslag = foreslaa_typer(kandidater)
    if not forslag:
        console.print("[yellow]Ingen forslag fra språkmodellen.[/yellow]")
        return

    navn = {k["product_id"]: k["name"] for k in kandidater}
    t = Table(title=f"Foreslåtte varetyper — {len(forslag)} produkter")
    t.add_column("Produkt")
    t.add_column("Varetype")
    for pid, typ in sorted(forslag.items(), key=lambda x: x[1]):
        t.add_row(navn[pid][:60], typ)
    console.print(t)

    if not args.apply:
        console.print("\n[dim]Forhåndsvisning. Kjør med [bold]--apply[/bold] "
                      "for å skrive til data/product_types.json.[/dim]")
        return

    mapping = json.loads(MAPPING_FILE.read_text()) if MAPPING_FILE.exists() else {}
    mapping.update({str(pid): typ for pid, typ in forslag.items()})
    MAPPING_FILE.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True)
    )
    _explicit_mapping.cache_clear()
    console.print(f"[green]✓[/green] Skrev {len(forslag)} produkter til "
                  f"{MAPPING_FILE}. Se over git-diffen før commit.")


if __name__ == "__main__":
    main()
