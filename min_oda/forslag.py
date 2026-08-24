"""LLM-forslag til handlelista: billigere alternativ per varetype (sparetips)
og nye varer å prøve. Begge er forankret i Odas katalogsøk — LLM-en velger
bare blant faktiske søketreff, så den kan aldri foreslå varer som ikke finnes.

Genereringen tar minutter (CLI-LLM + katalogsøk), så den kjører alltid i en
bakgrunnstråd: sidelast starter den automatisk når cachen i
data/llm_forslag.json (gitignored) er eldre enn et døgn, og fragmentet i
UI-et poller til den er ferdig. Ingen request venter på LLM-en — viktig bak
Cloudflare-tunnelen, som kutter svar etter ~100 s. Priser sammenlignes mot
sist betalt enhetspris (jf. prices.py), som kan være litt utdatert — UI-et
merker dem som ca.-priser.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

from . import llm
from .bakgrunnsjobb import Jobb
from .oda_client import search_products

DATA_DIR = Path(__file__).parent.parent / "data"
FORSLAG_FILE = DATA_DIR / "llm_forslag.json"

_MAX_SPARETIPS_RADER = 25
_MAX_KANDIDATER_PER_TYPE = 4
FERSK_TIMER = 24.0

_JOBB = Jobb("llm-forslag")


def er_i_gang() -> bool:
    return _JOBB.er_i_gang()


def siste_feil() -> str | None:
    return _JOBB.siste_feil()


def er_ferskt(max_age_hours: float = FERSK_TIMER) -> bool:
    f = load_forslag()
    if not f or not f.get("generert"):
        return False
    try:
        alder = datetime.now() - datetime.fromisoformat(f["generert"])
    except ValueError:
        return False
    return alder.total_seconds() < max_age_hours * 3600


def start_bakgrunnsjobb(rader, lines, chat=None, search=None):
    """Start generer() i en daemon-tråd (single-flight, jf. bakgrunnsjobb).
    `rader`/`lines` beregnes av kalleren i request-konteksten; tråden rører
    ingen web-cacher."""
    return _JOBB.start(lambda: generer(rader, lines, chat=chat, search=search))


def load_forslag() -> dict | None:
    """Sist genererte forslag, eller None hvis knappen aldri er trykket."""
    if not FORSLAG_FILE.exists():
        return None
    try:
        return json.loads(FORSLAG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _sparetips_kandidater(rader, search) -> list[dict]:
    """Katalogtreff som er billigere enn radens representant, per varetype.
    Søker på varetypens basenavn; identiske søk gjenbrukes på tvers av
    størrelses-suffikser (bleier-str5 og bleier-str6 søker begge «bleier»)."""
    sok_cache: dict[str, list[dict]] = {}
    kandidater: list[dict] = []
    for r in rader:
        if r.is_engangs or r.unit_price is None:
            continue
        base = str(r.key).split("-", 1)[0]
        if base not in sok_cache:
            try:
                sok_cache[base] = search(base)
            except httpx.HTTPError:
                sok_cache[base] = []
        billigere = [
            t for t in sok_cache[base]
            if t["price"] is not None
            and t["price"] < float(r.unit_price)
            and t["product_id"] != r.product_id
        ][:_MAX_KANDIDATER_PER_TYPE]
        if billigere:
            kandidater.append({
                "key": str(r.key),
                "fra_navn": str(r.product_name),
                "fra_pris": float(r.unit_price),
                "treff": billigere,
            })
        if len(kandidater) >= _MAX_SPARETIPS_RADER:
            break
    return kandidater


def _sparetips(rader, chat, search) -> list[dict] | None:
    """None = LLM-svikt (uparsebart/ingen svar), [] = genuint ingen tips."""
    kandidater = _sparetips_kandidater(rader, search)
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


def _kjopsprofil(lines: pd.DataFrame) -> str:
    """Kjøpsprofil-tekst for LLM-prompten. Poenget er å vise *valg*, ikke bare
    volum: variant-fordelingen innen hver varetype avslører bevisste
    preferanser (karbonadedeig framfor kjøttdeig, grovt framfor fint), og
    innsikt-signalene oppsummerer kjøkken, kokestil, pris og helse."""
    from .product_types import annotate
    from . import innsikt

    df = lines.dropna(subset=["product_id", "product_name"]).copy()
    df = annotate(df).dropna(subset=["varetype"])

    # Varetyper etter antall ordrer, med variant-fordeling der det er valg.
    per_variant = (
        df.groupby(["varetype", "product_name"])["order_id"].nunique()
        .reset_index(name="n")
        .sort_values(["varetype", "n"], ascending=[True, False])
    )
    type_orden = (
        df.groupby("varetype")["order_id"].nunique()
        .sort_values(ascending=False).head(25)
    )
    valg_linjer = []
    for typ in type_orden.index:
        varianter = per_variant[per_variant["varetype"] == typ].head(3)
        deler = ", ".join(f"{r.product_name} ({r.n} ordrer)"
                          for r in varianter.itertuples())
        valg_linjer.append(f"- {typ}: {deler}")

    kjokken = ", ".join(
        f"{c['name']} {c['pct']:.0f}%" for c in innsikt.cuisine_mix(lines)[:4]
        if c["pct"] >= 1
    )
    stil = innsikt.cooking_style(lines)
    pris = innsikt.price_consciousness(lines)
    helse = innsikt.health(lines)
    signaler = (
        f"Kjøkken (andel av forbruk): {kjokken or 'ukjent'}\n"
        f"Kokestil: {stil['raw_pct']:.0f}% råvarer, {stil['conv_pct']:.0f}% ferdigmat\n"
        f"Pris: {pris['cheap_pct']:.0f}% lavpris, {pris['eco_pct']:.0f}% av linjene økologisk\n"
        f"Helse: frukt/grønt {helse['veg']['pct']:.0f}%, kjøtt {helse['meat']['pct']:.0f}%, "
        f"fisk {helse['fish']['pct']:.0f}%, søtt/snacks {helse['sweets']['pct']:.0f}%"
    )
    return (
        "Varetyper etter kjøpsfrekvens, med hvilke varianter husholdningen "
        "faktisk velger:\n" + "\n".join(valg_linjer) + "\n\n" + signaler
    )


def _treff_matcher(sok: str, navn: str) -> bool:
    """Grov relevans-sjekk: minst ett søkeord (≥3 tegn) må stå i produktnavnet.
    Søk uten slike ord slipper gjennom (ingenting å sjekke mot)."""
    n = navn.lower()
    ord_ = [o for o in sok.lower().split() if len(o) >= 3]
    return not ord_ or any(o in n for o in ord_)


def _nye(lines: pd.DataFrame, chat, search) -> tuple[list[str], list[dict]] | None:
    """Nye varer å prøve. LLM-en leser først preferanser ut av kjøpsprofilen
    (variantvalg + innsikt-signaler) og foreslår så søkeord som matcher dem;
    treffene valideres mot katalogen. Returnerer (profil-observasjoner,
    forslag). None = LLM-svikt, tom forslagsliste = ingenting overlevde
    katalogvalideringen."""
    system = (
        "Du analyserer en husholdnings dagligvarehandel og foreslår nye varer "
        "å prøve. Les preferansene ut av valgene, ikke bare volumet: hvilken "
        "variant som vinner innen en varetype er et bevisst valg (karbonadedeig "
        "framfor kjøttdeig tyder på magrere kjøtt, grovt framfor fint, økologisk "
        "framfor ikke). Forslagene skal være interessante oppdagelser som "
        "matcher preferansene — ikke opplagt tilbehør til det de alt kjøper, og "
        "ikke varer de åpenbart har valgt bort."
    )
    user = (
        f"{_kjopsprofil(lines)}\n\n"
        "1. Formuler 3-5 korte observasjoner om husholdningens preferanser, "
        "på norsk, forankret i konkrete valg over.\n"
        "2. Foreslå 8 søkeord for nye varer de ikke kjøper i dag (norske "
        "dagligvarenavn, 1-3 ord, egnet som katalogsøk), hver med en kort "
        "begrunnelse som peker på en av observasjonene.\n"
        'Svar KUN med JSON: {"profil": ["<observasjon>", ...], '
        '"forslag": [{"sok": "<søkeord>", "begrunnelse": "<kort>"}]}'
    )
    data = llm.extract_json(chat(system, user, max_tokens=1200))
    if not isinstance(data, dict) or not isinstance(data.get("forslag"), list):
        return None
    profil = [str(p) for p in data.get("profil", []) if p][:5]
    out: list[dict] = []
    for d in data["forslag"][:8]:
        if not isinstance(d, dict) or not d.get("sok"):
            continue
        sok = str(d["sok"])
        try:
            treff = search(sok, limit=4)
        except httpx.HTTPError:
            continue
        # Katalogsøket kan gi urelaterte førstetreff (søk «linser» → sushi-
        # ingefær); da stemmer ikke begrunnelsen med varen. Krev at treffet
        # deler et ord med søket.
        t = next((t for t in treff if _treff_matcher(sok, t["name"])), None)
        if t is None:
            continue
        out.append({
            "product_id": t["product_id"],
            "navn": t["name"],
            "pris": t["price"],
            "image": t.get("image"),
            "begrunnelse": str(d.get("begrunnelse") or ""),
        })
    return profil, out


def generer(rader, lines: pd.DataFrame,
            chat=None, search=None) -> dict:
    """Generer og lagre forslag. `chat`/`search` kan injiseres i tester.
    Ved LLM-svikt returneres et feil-dict uten å røre forrige cache."""
    chat = chat or llm.chat
    search = search or search_products
    if not llm.enabled() and chat is llm.chat:
        return {"feil": "Ingen LLM-provider tilgjengelig (jf. LLM_PROVIDER i .env)."}

    sparetips = _sparetips(rader, chat, search)
    nye_resultat = _nye(lines, chat, search)
    if sparetips is None and nye_resultat is None:
        return {"feil": "Fikk ikke brukbart svar fra språkmodellen. Prøv igjen."}
    profil, nye = nye_resultat if nye_resultat else ([], [])

    forslag = {
        "generert": datetime.now().isoformat(timespec="minutes"),
        "provider": llm.provider_label(),
        "sparetips": sparetips or [],
        "profil": profil,
        "nye": nye,
    }
    FORSLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    FORSLAG_FILE.write_text(json.dumps(forslag, ensure_ascii=False, indent=1))
    return forslag
