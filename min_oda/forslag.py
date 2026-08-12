"""LLM-forslag til handlelista: billigere alternativ per varetype (sparetips)
og nye varer å prøve. Begge er forankret i Odas katalogsøk — LLM-en velger
bare blant faktiske søketreff, så den kan aldri foreslå varer som ikke finnes.

Genereres on-demand fra en knapp i UI-et og caches i data/llm_forslag.json
(gitignored), så sidevisninger aldri koster LLM-kall. Priser sammenlignes
mot sist betalt enhetspris (jf. prices.py), som kan være litt utdatert —
UI-et merker dem som ca.-priser.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

from . import llm
from .oda_client import search_products

DATA_DIR = Path(__file__).parent.parent / "data"
FORSLAG_FILE = DATA_DIR / "llm_forslag.json"

_MAX_SPARETIPS_RADER = 25
_MAX_KANDIDATER_PER_TYPE = 4


def load_forslag() -> dict | None:
    """Sist genererte forslag, eller None hvis knappen aldri er trykket."""
    if not FORSLAG_FILE.exists():
        return None
    try:
        return json.loads(FORSLAG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _sparetips_kandidater(rows: list[dict], search) -> list[dict]:
    """Katalogtreff som er billigere enn radens representant, per varetype.
    Søker på varetypens basenavn; identiske søk gjenbrukes på tvers av
    størrelses-suffikser (bleier-str5 og bleier-str6 søker begge «bleier»)."""
    sok_cache: dict[str, list[dict]] = {}
    kandidater: list[dict] = []
    for r in rows:
        if r.get("is_engangs") or r.get("unit_price") is None:
            continue
        base = str(r["key"]).split("-", 1)[0]
        if base not in sok_cache:
            try:
                sok_cache[base] = search(base)
            except httpx.HTTPError:
                sok_cache[base] = []
        billigere = [
            t for t in sok_cache[base]
            if t["price"] is not None
            and t["price"] < float(r["unit_price"])
            and t["product_id"] != r["product_id"]
        ][:_MAX_KANDIDATER_PER_TYPE]
        if billigere:
            kandidater.append({
                "key": str(r["key"]),
                "fra_navn": str(r["product_name"]),
                "fra_pris": float(r["unit_price"]),
                "treff": billigere,
            })
        if len(kandidater) >= _MAX_SPARETIPS_RADER:
            break
    return kandidater


def _sparetips(rows: list[dict], chat, search) -> list[dict] | None:
    """None = LLM-svikt (uparsebart/ingen svar), [] = genuint ingen tips."""
    kandidater = _sparetips_kandidater(rows, search)
    if not kandidater:
        return []
    listing = "\n".join(
        f"varetype {k['key']}: kjøper i dag «{k['fra_navn']}» "
        f"(ca. {k['fra_pris']:.2f} kr/stk). Billigere katalogtreff: "
        + "; ".join(f"[{t['product_id']}] {t['name']} ({t['price']:.2f} kr)"
                    for t in k["treff"])
        for k in kandidater
    )
    system = (
        "Du vurderer om billigere dagligvarer fra en katalog er fullgode "
        "erstatninger for det en husholdning kjøper i dag. Vær streng: et "
        "substitutt må være samme type vare i sammenlignbar mengde. Feil "
        "variantklasse (laktosefri vs. vanlig, grovt vs. fint, feil "
        "bleiestørrelse i varetype-nøkkelen) er ikke et substitutt. Vurder "
        "pakningsstørrelse ut fra navnene — en lavere stykkpris for en mye "
        "mindre pakning er ikke et sparetips."
    )
    user = (
        f"{listing}\n\n"
        "Velg for hver varetype maks ett produkt som er et reelt og billigere "
        "substitutt, og gi en kort begrunnelse på norsk (én setning). Hopp "
        "over varetyper uten godt alternativ.\n"
        'Svar KUN med JSON: [{"key": "<varetype>", "product_id": <int>, '
        '"begrunnelse": "<kort>"}]'
    )
    data = llm.extract_json(chat(system, user, max_tokens=1500))
    if not isinstance(data, list):
        return None
    treff_per_key = {k["key"]: {t["product_id"]: t for t in k["treff"]}
                     for k in kandidater}
    fra_per_key = {k["key"]: k for k in kandidater}
    out: list[dict] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        key = str(d.get("key") or "")
        try:
            pid = int(d.get("product_id"))
        except (TypeError, ValueError):
            continue
        # Forankring: bare produkter som faktisk var blant kandidatene.
        t = treff_per_key.get(key, {}).get(pid)
        if not t:
            continue
        k = fra_per_key[key]
        out.append({
            "key": key,
            "fra_navn": k["fra_navn"],
            "fra_pris": k["fra_pris"],
            "product_id": pid,
            "navn": t["name"],
            "pris": t["price"],
            "image": t.get("image"),
            "begrunnelse": str(d.get("begrunnelse") or ""),
        })
    return out


def _nye(lines: pd.DataFrame, chat, search) -> list[dict] | None:
    """Nye varer å prøve: LLM foreslår søkeord ut fra kjøpsprofilen, treffene
    valideres mot katalogen, første tilgjengelige treff per søk vises.
    None = LLM-svikt, [] = ingen forslag overlevde katalogvalideringen."""
    top = (
        lines.dropna(subset=["product_name"])
        .groupby("product_name")["order_id"]
        .nunique()
        .sort_values(ascending=False)
        .head(40)
    )
    profil = "\n".join(f"- {navn} ({n} ordrer)" for navn, n in top.items())
    system = (
        "Du foreslår nye dagligvarer for en husholdning, basert på hva den "
        "faktisk kjøper. Foreslå konkrete varer som passer matprofilen men "
        "som ikke er på listen — hull i sortimentet, naturlige tilbehør, "
        "eller varer som løfter retter de tydelig lager."
    )
    user = (
        f"Husholdningens mest kjøpte varer:\n{profil}\n\n"
        "Foreslå 5 søkeord for varer de ikke kjøper i dag (norske "
        "dagligvarenavn, 1-3 ord, egnet som katalogsøk), hver med en kort "
        "begrunnelse på norsk knyttet til profilen.\n"
        'Svar KUN med JSON: [{"sok": "<søkeord>", "begrunnelse": "<kort>"}]'
    )
    data = llm.extract_json(chat(system, user, max_tokens=800))
    if not isinstance(data, list):
        return None
    out: list[dict] = []
    for d in data[:5]:
        if not isinstance(d, dict) or not d.get("sok"):
            continue
        try:
            treff = search(str(d["sok"]), limit=1)
        except httpx.HTTPError:
            continue
        if not treff:
            continue
        t = treff[0]
        out.append({
            "product_id": t["product_id"],
            "navn": t["name"],
            "pris": t["price"],
            "image": t.get("image"),
            "begrunnelse": str(d.get("begrunnelse") or ""),
        })
    return out


def generer(rows: list[dict], lines: pd.DataFrame,
            chat=None, search=None) -> dict:
    """Generer og lagre forslag. `chat`/`search` kan injiseres i tester.
    Ved LLM-svikt returneres et feil-dict uten å røre forrige cache."""
    chat = chat or llm.chat
    search = search or search_products
    if not llm.enabled() and chat is llm.chat:
        return {"feil": "Ingen LLM-provider tilgjengelig (jf. LLM_PROVIDER i .env)."}

    sparetips = _sparetips(rows, chat, search)
    nye = _nye(lines, chat, search)
    if sparetips is None and nye is None:
        return {"feil": "Fikk ikke brukbart svar fra språkmodellen. Prøv igjen."}

    forslag = {
        "generert": datetime.now().isoformat(timespec="minutes"),
        "provider": llm.provider_label(),
        "sparetips": sparetips or [],
        "nye": nye or [],
    }
    FORSLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    FORSLAG_FILE.write_text(json.dumps(forslag, ensure_ascii=False, indent=1))
    return forslag
