"""Handlelista: hva som bør handles, som ferdige rader.

Hele beregningen bak /handleliste bor her — kadens og kuratering
(build_list), kurv-diff (cart_diff), representanter, priser, bilder,
varianter og engangsvarer — bak ett kall. Rutene i web/main.py rendrer
resultatet, de regner ikke selv.

To ting som før lå spredt utover rutene, og som nå er innebygd:
rekkefølgen curate → compute_diff (compute_diff krever kolonner bare
curate legger på), og «foreslått antall», som regnes ett sted
(build_list.foreslatt_antall).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import blocklist, engangsvarer, representatives
from .build_list import curate, foreslatt_antall
from .cart_diff import compute_diff
from .variants import variants_for_type

DEFAULT_CYCLE = 7
DEFAULT_TOP = 40
DEFAULT_MAX_PER_CAT = 8
VARIANT_LIMIT = 10

EMPTY_CART = pd.DataFrame(
    columns=["product_id", "product_name", "category", "quantity", "varetype"]
)

_NB_MANEDER_KORT = ["", "jan", "feb", "mar", "apr", "mai", "jun",
                    "jul", "aug", "sep", "okt", "nov", "des"]


def kort_dato(ts) -> str:
    """Norsk kort datoform: '14. mai' i år, '14. mai 2025' ellers."""
    if ts is None or pd.isna(ts):
        return "—"
    d = pd.Timestamp(ts)
    if d.year == pd.Timestamp.now().year:
        return f"{d.day}. {_NB_MANEDER_KORT[d.month]}"
    return f"{d.day}. {_NB_MANEDER_KORT[d.month]} {d.year}"


@dataclass(frozen=True)
class Valg:
    """Filtrene brukeren styrer lista med. `new_list=True` gir hele den
    kuraterte lista, ellers diffes den mot kurven."""

    cycle: int = DEFAULT_CYCLE
    top: int = DEFAULT_TOP
    max_per_cat: int = DEFAULT_MAX_PER_CAT
    search: str = ""
    new_list: bool = False
    top_up: bool = False

    @classmethod
    def fra_form(cls, form) -> Valg:
        """Les filtrene fra form-data (HTMX sender dem via hx-include).
        Samme filtersett som query-parametrene på GET /handleliste."""
        def heltall(navn: str, standard: int) -> int:
            try:
                return int(form.get(navn) or standard)
            except (TypeError, ValueError):
                return standard

        def flagg(navn: str) -> bool:
            return str(form.get(navn) or "").lower() in {"true", "on", "1"}

        return cls(
            cycle=heltall("cycle", DEFAULT_CYCLE),
            top=heltall("top", DEFAULT_TOP),
            max_per_cat=heltall("max_per_cat", DEFAULT_MAX_PER_CAT),
            search=str(form.get("search") or ""),
            new_list=flagg("new_list"),
            top_up=flagg("top_up"),
        )


@dataclass
class Kilder:
    """Oppslag web-laget cacher mellom requester: sist betalt pris, bilde
    og produkt-id-ene som er med ved default-filtre. Tomme er lovlige —
    da mangler radene pris og bilde, og ingen markeres som ekstra."""

    priser: dict[int, float] = field(default_factory=dict)
    bilder: dict[int, str] = field(default_factory=dict)
    baseline_ids: set[int] | frozenset[int] = frozenset()


@dataclass
class Rad:
    """Én rad i handlelista, ferdig for `_list_row.html`.

    Én form for både kadens-rader og engangsvarer: før hadde de to ulike
    nøkkelsett, og templaten lente seg på at Jinja gjør manglende nøkler
    til usann."""

    product_id: int
    key: str
    product_name: str
    category: str = ""
    image: str | None = None
    unit_price: float | None = None
    line_cost: float | None = None
    forslag: int = 1
    i_kurv: int | None = None
    mangler: int | None = None
    qty: int = 1
    days_since: int | None = None
    last_label: str = ""
    is_extra: bool = False
    is_added_variant: bool = False
    is_chosen: bool = False
    is_engangs: bool = False
    variants: list[dict] = field(default_factory=list)


@dataclass
class Liste:
    rader: list[Rad] = field(default_factory=list)
    kurv_antall: int = 0
    ekstra_antall: int = 0

    @property
    def total(self) -> float:
        """Ca.-sum for lista. Avrundet per rad, som JS-en i base.html."""
        return float(
            sum(round(r.line_cost) for r in self.rader if r.line_cost is not None)
        )


def bygg(
    lines: pd.DataFrame,
    valg: Valg | None = None,
    kurv: pd.DataFrame | None = None,
    kilder: Kilder | None = None,
    today: pd.Timestamp | None = None,
) -> Liste:
    """Bygg hele handlelista.

    `kurv` brukes bare når `valg.new_list` er False; da faller radene som
    kurven dekker bort (jf. cart_diff.compute_diff). Blokkeringer, valgte
    representanter og engangsvarer leses fra sine JSON-filer."""
    valg = valg or Valg()
    kilder = kilder or Kilder()
    valgte = representatives.chosen_representatives()

    kurv_df = None if valg.new_list else (kurv if kurv is not None else EMPTY_CART)
    kurv_antall = (
        int(kurv_df["quantity"].sum())
        if kurv_df is not None and not kurv_df.empty
        else 0
    )

    ideal = curate(
        lines,
        list_cycle_days=valg.cycle,
        top_n=valg.top,
        max_per_category=valg.max_per_cat,
        blocked=blocklist.blocked_ids(),
        blocked_types=blocklist.blocked_types(),
        chosen=valgte,
        today=today,
    )
    if ideal.empty:
        # Engangsvarer skal vises selv uten kadens-kandidater.
        return Liste(_engangs_rader(valg, kurv_df, kilder), kurv_antall, 0)

    kilder = _med_valgte_snapshot(kilder, valgte)
    if kurv_df is not None:
        ideal = compute_diff(ideal, kurv_df, top_up=valg.top_up)
    ideal = _sok_filter(ideal, valg.search)

    blokkerte = blocklist.blocked_ids()
    rader: list[Rad] = []
    for _, r in ideal.iterrows():
        pid = int(r["product_id"])
        key = str(r["key"])
        er_valgt = key in valgte and int(valgte[key]["product_id"]) == pid
        # Valgt representant: ingen variant-dropdown — katalogvaren finnes
        # ikke blant de historiske variantene.
        varianter = [] if er_valgt else varianter_for(lines, key, blokkerte)
        rader.append(_rad(
            r, pid, str(r["product_name"]), str(r["category"]),
            valg, kurv_df, kilder, varianter, is_chosen=er_valgt,
        ))

    ekstra = sum(1 for r in rader if r.is_extra)
    rader.extend(_engangs_rader(valg, kurv_df, kilder))
    return Liste(rader, kurv_antall, ekstra)


def variant_rad(
    lines: pd.DataFrame,
    kadens: pd.DataFrame,
    key: str,
    pid: int,
    valg: Valg | None = None,
    kurv: pd.DataFrame | None = None,
    kilder: Kilder | None = None,
    is_added_variant: bool = False,
) -> Rad | None:
    """Én rad for (varetype, produkt) — variant-bytte og variant-add.

    `kadens` er compute_cadence(by_type=True). None hvis varetypen eller
    produktet ikke finnes i historikken."""
    valg = valg or Valg()
    kilder = kilder or Kilder()
    sub = kadens[kadens["key"] == key]
    if sub.empty:
        return None
    kadens_rad = sub.iloc[0]

    varianter = varianter_for(lines, key, blocklist.blocked_ids())
    # Variantlisten er kuttet til VARIANT_LIMIT, så fall tilbake til lines
    # for navn og kategori.
    historikk = lines[lines["product_id"].astype(int) == pid].head(1)
    treff = next((v for v in varianter if v["product_id"] == pid), None)
    if treff:
        product_name = treff["product_name"]
    elif historikk.empty:
        return None
    else:
        product_name = str(historikk["product_name"].iloc[0])
    category = str(historikk["category"].iloc[0]) if not historikk.empty else ""

    kurv_df = None if valg.new_list else (kurv if kurv is not None else EMPTY_CART)
    return _rad(
        kadens_rad, pid, product_name, category, valg, kurv_df, kilder,
        varianter, is_added_variant=is_added_variant,
    )


def varianter_for(
    lines: pd.DataFrame, key: str, blokkerte: set[int] | frozenset[int] | None = None
) -> list[dict]:
    """Topp varianter for en varetype, som (product_id, product_name)."""
    df = variants_for_type(
        lines, key, limit=VARIANT_LIMIT,
        blocked=blocklist.blocked_ids() if blokkerte is None else blokkerte,
    )
    return [
        {"product_id": int(r["product_id"]), "product_name": str(r["product_name"])}
        for _, r in df.iterrows()
    ]


def _rad(
    kadens_rad: pd.Series,
    pid: int,
    product_name: str,
    category: str,
    valg: Valg,
    kurv: pd.DataFrame | None,
    kilder: Kilder,
    varianter: list[dict],
    is_added_variant: bool = False,
    is_chosen: bool = False,
) -> Rad:
    antall = foreslatt_antall(
        kadens_rad["median_days"], kadens_rad["avg_qty_per_event"], valg.cycle
    )
    if kurv is None:
        i_kurv = mangler = None
        qty = antall
    else:
        i_kurv = _kurv_antall(kurv, str(kadens_rad["key"]))
        mangler = max(0, antall - i_kurv)
        qty = mangler
    unit_price = kilder.priser.get(pid)
    return Rad(
        product_id=pid,
        key=str(kadens_rad["key"]),
        product_name=product_name,
        category=category,
        image=kilder.bilder.get(pid),
        unit_price=unit_price,
        line_cost=unit_price * qty if unit_price is not None else None,
        forslag=antall,
        i_kurv=i_kurv,
        mangler=mangler,
        qty=qty,
        days_since=(
            int(kadens_rad["days_since"])
            if pd.notna(kadens_rad.get("days_since")) else None
        ),
        last_label=kort_dato(kadens_rad["last"]),
        is_extra=pid not in kilder.baseline_ids,
        is_added_variant=is_added_variant,
        is_chosen=is_chosen,
        variants=varianter,
    )


def _engangs_rader(
    valg: Valg, kurv: pd.DataFrame | None, kilder: Kilder
) -> list[Rad]:
    """Rader for engangsvarer fra katalogsøket (jf. engangsvarer.py).

    Ingen kadens — antallet er det brukeren har lagt inn lokalt, og
    dekningen telles per product_id, ikke per varetype som for de andre
    radene. Det er med vilje: en engangsvare er valgt som *den varen*, og
    står utenfor varetype-logikken. Sist betalt pris vinner over
    søketreff-snapshotet, som for valgte representanter."""
    rader: list[Rad] = []
    for item in engangsvarer.list_items():
        if valg.search and valg.search.lower() not in item["name"].lower():
            continue
        pid = item["product_id"]
        antall = item["qty"]
        if kurv is None:
            i_kurv = mangler = None
            qty = antall
        else:
            sub = kurv[kurv["product_id"].astype(int) == pid] if not kurv.empty else kurv
            i_kurv = 0 if sub.empty else int(sub["quantity"].sum())
            mangler = max(0, antall - i_kurv)
            qty = mangler
        pris = kilder.priser.get(pid, item["price"])
        unit_price = float(pris) if pris is not None else None
        rader.append(Rad(
            product_id=pid,
            key="engangs",
            product_name=item["name"],
            image=item["image"] or None,
            unit_price=unit_price,
            line_cost=unit_price * qty if unit_price is not None else None,
            forslag=antall,
            i_kurv=i_kurv,
            mangler=mangler,
            qty=qty,
            is_engangs=True,
        ))
    return rader


def _med_valgte_snapshot(kilder: Kilder, valgte: dict) -> Kilder:
    """Katalogvarer finnes ikke i historikken — fyll pris og bilde fra
    søketreff-snapshotet. setdefault: en ekte sist-betalt-pris vinner.
    Kopierer, så de cachede oppslagene ikke forurenses."""
    if not valgte:
        return kilder
    priser = dict(kilder.priser)
    bilder = dict(kilder.bilder)
    for c in valgte.values():
        pid = int(c["product_id"])
        if c.get("price") is not None:
            priser.setdefault(pid, float(c["price"]))
        if c.get("image"):
            bilder.setdefault(pid, str(c["image"]))
    return Kilder(priser, bilder, kilder.baseline_ids)


def _kurv_antall(kurv: pd.DataFrame, key: str) -> int:
    """Sum antall i kurven for varetypen. Teller per varetype, ikke per
    product_id — samme semantikk som compute_diff, så en substituerende
    variant (buksebleier for bleier) regnes som dekning."""
    if kurv.empty:
        return 0
    sub = kurv[kurv["varetype"] == key]
    return 0 if sub.empty else int(sub["quantity"].sum())


def _sok_filter(ideal: pd.DataFrame, search: str) -> pd.DataFrame:
    if not search or ideal.empty:
        return ideal
    s = search.lower()
    mask = (
        ideal["key"].astype(str).str.lower().str.contains(s, na=False)
        | ideal["product_name"].astype(str).str.lower().str.contains(s, na=False)
        | ideal["category"].astype(str).str.lower().str.contains(s, na=False)
    )
    return ideal[mask].reset_index(drop=True)
