"""LLM-lag for /innsikt: navngir mønstre tallene ikke kan navngi selv.

Samme arbeidsdeling som forslag.py — LLM-en får aldri regne:
- Måltidsmønstre: modellen leser ordre-kurver (varetyper per handletur, med
  allestedsnærværende stifter filtrert bort) og foreslår navngitte mønstre
  («taco-fredag»). Antall ordrer, sist sett og rytme regnes deterministisk
  etterpå, og mønstre uten reell støtte i ordrene forkastes.
- Siden sist: nye gjengangere, stifter på vei ut og kategori-skift beregnes
  deterministisk (_fakta_siden_sist); modellen formulerer bare setningene.

Kjøres i bakgrunnen (jf. generering.py) og caches i data/innsikt_llm.json
(gitignored), samme livssyklus som forslagene på handlelista.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from . import llm
from .filer import DATA_DIR
from .generering import Generering
from .product_types import annotate

_STIFT_ANDEL = 0.5    # varetyper i over halvparten av ordrene skjules for LLM-en
_MAX_ORDRER = 100
_MIN_STOTTE = 3       # mønstre må finnes i minst så mange ordrer

# Bakgrunnstråd, cache-fil, ferskhet og template-kontekst: jf. generering.py.
GENERERING = Generering("llm-innsikt", DATA_DIR / "innsikt_llm.json")

_UKEDAGER = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]


def _ordre_typer(df: pd.DataFrame) -> list[dict]:
    """[{order_id, date, typer}] per ordre, nyeste først. `typer` er det
    komplette settet — stift-filtreringen skjer først i promptbyggingen,
    så mønster-verifiseringen kan matche mot hele kurven."""
    d = df.dropna(subset=["varetype", "order_id"])
    if d.empty:
        return []
    per = (
        d.groupby("order_id")
        .agg(date=("date", "first"), typer=("varetype", lambda s: set(s)))
        .reset_index()
        .sort_values("date", ascending=False)
    )
    return per.to_dict("records")


def _monstre(df: pd.DataFrame, chat) -> list[dict] | None:
    """Navngitte måltids-/rutinemønstre. None = LLM-svikt, [] = ingen
    mønstre overlevde støtte-verifiseringen."""
    ordrer = _ordre_typer(df)
    if len(ordrer) < _MIN_STOTTE:
        return []
    frekvens = Counter(t for o in ordrer for t in o["typer"])
    stifter = {t for t, n in frekvens.items() if n / len(ordrer) > _STIFT_ANDEL}

    linjer = []
    for o in ordrer[:_MAX_ORDRER]:
        typer = sorted(o["typer"] - stifter)
        if len(typer) < 3:
            continue
        dato = pd.Timestamp(o["date"])
        linjer.append(
            f"{dato.date()} ({_UKEDAGER[dato.dayofweek]}): {', '.join(typer)}"
        )
    if len(linjer) < _MIN_STOTTE:
        return []

    system = (
        "Du finner tilbakevendende måltids- og handlemønstre i en "
        "husholdnings ordrehistorikk. Et mønster er en kombinasjon av "
        "varetyper som går igjen sammen og ser ut som en gjenkjennelig rett "
        "eller rutine (taco-kveld, søndagsfrokost). Gi hvert mønster et "
        "kort, nøkternt norsk navn."
    )
    user = (
        "Ordrene under viser varetyper per handletur (dagligdagse basisvarer "
        "er filtrert bort). Finn 3-6 mønstre.\n"
        "Bruk varetype-navnene NØYAKTIG som de står i ordrene.\n\n"
        + "\n".join(linjer)
        + '\n\nSvar KUN med JSON: [{"navn": "<kort navn>", '
        '"varetyper": ["<varetype>", ...], "kommentar": "<én setning>"}]'
    )
    data = llm.extract_json(chat(system, user, max_tokens=1000))
    if not isinstance(data, list):
        return None

    out: list[dict] = []
    for m in data:
        if not isinstance(m, dict):
            continue
        vt = [str(t) for t in (m.get("varetyper") or []) if t]
        if len(vt) < 2:
            continue
        # Verifiser mot de faktiske kurvene: nesten alle varetypene må
        # opptre sammen. Tall LLM-en måtte påstå ignoreres — vi regner selv.
        krav = max(2, len(vt) - 1)
        treff = [o for o in ordrer if len(set(vt) & o["typer"]) >= krav]
        if len(treff) < _MIN_STOTTE:
            continue
        datoer = sorted(pd.Timestamp(o["date"]) for o in treff)
        gap = [(b - a).days for a, b in zip(datoer, datoer[1:])]
        intervall = int(pd.Series(gap).median()) if gap else None
        out.append({
            "navn": str(m.get("navn") or "").strip() or "(uten navn)",
            "varetyper": vt,
            "n_ordrer": len(treff),
            "sist": datoer[-1].date().isoformat(),
            "intervall_dager": intervall,
            "kommentar": str(m.get("kommentar") or ""),
        })
    out.sort(key=lambda m: -m["n_ordrer"])
    return out


def _fakta_siden_sist(df: pd.DataFrame, today: pd.Timestamp) -> list[str]:
    """Deterministiske fakta-linjer for «siden sist»: nye gjengangere,
    stifter på vei ut, og kategori-skift siste 30 d mot 30 d før."""
    d = df.dropna(subset=["varetype", "date"])
    fakta: list[str] = []
    if d.empty:
        return fakta

    per_type = d.groupby("varetype").agg(
        forste=("date", "min"), siste=("date", "max"),
        n_ordrer=("order_id", "nunique"),
    )
    navn_per_type = (
        d.groupby(["varetype", "product_name"])["order_id"].nunique()
        .reset_index(name="n")
        .sort_values("n", ascending=False)
        .drop_duplicates("varetype")
        .set_index("varetype")["product_name"]
    )

    nye = per_type[
        (per_type["forste"] >= today - pd.Timedelta(days=60))
        & (per_type["n_ordrer"] >= 3)
    ]
    for typ, r in nye.iterrows():
        fakta.append(
            f"Ny gjenganger: {navn_per_type.get(typ, typ)} ({typ}), "
            f"{r['n_ordrer']} ordrer siste 60 dager"
        )

    for typ, r in per_type[per_type["n_ordrer"] >= 6].iterrows():
        datoer = sorted(d[d["varetype"] == typ]
                        .groupby("order_id")["date"].first())
        gap = [(b - a).days for a, b in zip(datoer, datoer[1:])]
        median = float(pd.Series(gap).median()) if gap else 0.0
        siden = (today - pd.Timestamp(r["siste"])).days
        if 0 < median <= 45 and siden > 2 * median:
            fakta.append(
                f"Stift på vei ut: {navn_per_type.get(typ, typ)} ({typ}), "
                f"kjøpt ca. hver {median:.0f}. dag men sist for {siden} dager siden"
            )

    naa = d[d["date"] >= today - pd.Timedelta(days=30)]
    forr = d[(d["date"] >= today - pd.Timedelta(days=60))
             & (d["date"] < today - pd.Timedelta(days=30))]
    if not naa.empty and not forr.empty and "line_total" in d.columns:
        diff = (
            naa.groupby("category")["line_total"].sum()
            .sub(forr.groupby("category")["line_total"].sum(), fill_value=0)
        )
        for kat, kr in diff.reindex(diff.abs().sort_values(ascending=False).index).head(2).items():
            if abs(kr) >= 200:
                retning = "opp" if kr > 0 else "ned"
                fakta.append(
                    f"Kategori-skift: {kat} {retning} {abs(kr):.0f} kr "
                    f"siste 30 dager mot 30 dager før"
                )
    return fakta


def _siden_sist(df: pd.DataFrame, chat, today: pd.Timestamp) -> str | None:
    """2-4 setninger om hva som har endret seg, formulert av LLM-en fra
    deterministiske fakta. None = LLM-svikt, '' = ingenting å melde."""
    fakta = _fakta_siden_sist(df, today)
    if not fakta:
        return ""
    system = (
        "Du oppsummerer endringer i en husholdnings dagligvarehandel. "
        "Nøkternt norsk (bokmål), ingen utropstegn, ingen råd. Bruk KUN "
        "fakta du får — ingen egne tall eller antakelser."
    )
    user = (
        "Skriv 2-4 korte setninger i løpende tekst av disse funnene, med "
        "konkrete navn. Uinteressante funn kan utelates.\n\n"
        + "\n".join(f"- {f}" for f in fakta)
    )
    ut = chat(system, user, max_tokens=400)
    return ut.strip() if ut else None


def generer(lines: pd.DataFrame, chat=None,
            today: pd.Timestamp | None = None) -> dict:
    """Generer og lagre innsikt. `chat`/`today` kan injiseres i tester
    (`today` må være tz-bevisst UTC, jf. data_loader.load_lines).
    Ved LLM-svikt returneres et feil-dict uten å røre forrige cache."""
    chat = chat or llm.chat
    if feil := llm.mangler_provider(chat):
        return {"feil": feil}
    # `today` er tz-bevisst UTC, samme konvensjon som load_lines og
    # restock.compute_cadence. Ett tidsregime i hele analysekjeden.
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.now(tz="UTC")

    df = annotate(lines)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    monstre = _monstre(df, chat)
    siden = _siden_sist(df, chat, today.normalize())
    if monstre is None and siden is None:
        return {"feil": "Fikk ikke brukbart svar fra språkmodellen. Prøv igjen."}

    return GENERERING.lagre({
        "monstre": monstre or [],
        "siden_sist": siden or "",
    })


def start_bakgrunnsjobb(lines: pd.DataFrame, chat=None):
    """Start generer() i en daemon-tråd (single-flight)."""
    return GENERERING.start(lambda: generer(lines, chat=chat))
