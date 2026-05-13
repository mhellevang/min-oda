"""Streamlit-GUI for Oda-analyse.

Kjør:  uv run streamlit run app.py

Multi-page-app med tre sider (handleliste / diff mot kurv / restock).
Felles sidebar med slidere som styrer handleliste-genereringen.
Hver side har egen URL — bokmerkbar og browser-back/forward virker.
"""

from __future__ import annotations

import streamlit as st

from build_list import add_products, create_list, curate, load
from cart_diff import compute_diff, fetch_cart
from fetch_orders import build_client
from restock import compute_cadence

st.set_page_config(page_title="Oda-analyse", page_icon="🛒", layout="wide")


@st.cache_data(ttl=600, show_spinner="Leser ordrehistorikk …")
def cached_lines():
    return load()


@st.cache_data(ttl=120, show_spinner="Henter handlekurv …")
def cached_cart():
    return fetch_cart(build_client())


lines = cached_lines()

st.sidebar.header("Innstillinger")
st.sidebar.caption(
    "Styrer hva som havner på handlelisten din. Restock-siden påvirkes "
    "ikke av disse."
)
cycle = st.sidebar.slider(
    "Listesyklus (dager)", 7, 28, 14, step=1,
    help=(
        "Hvor mange dager du planlegger å handle for. Bruker du "
        "vanligvis én melk i uka og setter syklus til 14, foreslår "
        "appen 2 melk."
    ),
)
top_n = st.sidebar.slider(
    "Maks varetyper på liste", 20, 120, 40, step=5,
    help=(
        "Hvor mange forskjellige varer som maks kan stå på listen. "
        "Skru opp hvis listen føles for kort eller du savner noe."
    ),
)
max_per_cat = st.sidebar.slider(
    "Maks per kategori", 4, 20, 8,
    help=(
        "Hvor mange varer som maks får plass innenfor hver Oda-"
        "kategori (Meieri, Pålegg, …). Hindrer at én stor kategori "
        "tar all plassen. Skru opp hvis du savner noe i en bestemt "
        "kategori."
    ),
)
st.sidebar.caption(
    f"{len(lines)} linjer · {lines['order_id'].nunique()} ordrer"
)
if st.sidebar.button("🔄 Tøm cache"):
    st.cache_data.clear()
    st.rerun()


def _filter(df, search, columns):
    s = search.lower()
    mask = None
    for col in columns:
        col_mask = df[col].astype(str).str.lower().str.contains(s, na=False)
        mask = col_mask if mask is None else (mask | col_mask)
    return df[mask].reset_index(drop=True)


def page_handleliste():
    st.header("📋 Foreslått handleliste")
    st.caption(
        f"Syklus {cycle} d · maks {top_n} varetyper · {max_per_cat}/kategori"
    )

    ideal = curate(
        lines,
        list_cycle_days=cycle,
        top_n=top_n,
        max_per_category=max_per_cat,
    )
    if ideal.empty:
        st.warning("Ingen kandidater fra datasettet.")
        return

    search = st.text_input(
        "🔍 Søk",
        placeholder="Filtrer på varetype, produkt eller kategori",
        key="list_search",
    )
    if search:
        ideal = _filter(ideal, search, ["key", "product_name", "category"])
    if ideal.empty:
        st.info("Ingen treff.")
        return

    view = ideal[
        ["category", "key", "product_name", "foreslått_antall",
         "median_days", "last"]
    ].copy()
    view["median_days"] = view["median_days"].apply(
        lambda d: f"hver {int(round(d))}. d"
    )
    view["last"] = view["last"].dt.date
    view.columns = ["Kategori", "Varetype", "Produkt", "Antall",
                    "Kadens", "Sist kjøpt"]
    edited = st.data_editor(
        view,
        width="stretch",
        hide_index=True,
        column_config={
            "Antall": st.column_config.NumberColumn(
                min_value=0, step=1, format="%d",
                help="Sett til 0 for å droppe varen",
            ),
        },
        disabled=["Kategori", "Varetype", "Produkt", "Kadens",
                  "Sist kjøpt"],
        key="list_editor",
    )

    st.divider()
    col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
    title = col1.text_input("Listetittel", value="Ukehandel — familien")
    if col2.button("📤 Opprett liste på Oda", type="primary",
                   width="stretch"):
        items = [
            (int(ideal.iloc[i]["product_id"]),
             int(edited.iloc[i]["Antall"]))
            for i in range(len(ideal))
            if int(edited.iloc[i]["Antall"]) > 0
        ]
        if not items:
            st.warning("Ingen varer å legge til (alle på 0).")
            return
        with st.spinner("Oppretter …"):
            client = build_client()
            result = create_list(
                client, title, f"Faste varer · {cycle} d syklus"
            )
            if not result:
                st.error("Kunne ikke opprette listen.")
                return
            list_id = result["id"]
            ok = add_products(client, list_id, items)
            st.success(
                f"La til {ok}/{len(items)} varer. "
                f"[Åpne listen](https://oda.com/no/account/lists/details/{list_id}/)"
            )


def page_diff():
    st.header("🛒 Diff mot handlekurv")

    top_up = st.checkbox(
        "Inkluder også varer som er i kurv, men i for lavt antall",
        value=False,
        help=(
            "Vanligvis vises bare varer som mangler helt fra kurven. "
            "Slå på for også å se varer du har for få av."
        ),
    )

    ideal = curate(
        lines,
        list_cycle_days=cycle,
        top_n=top_n,
        max_per_category=max_per_cat,
    )
    cart = cached_cart()
    cart_total = int(cart["quantity"].sum()) if not cart.empty else 0

    st.caption(f"{cart_total} varer i kurven · syklus {cycle} d")

    missing = compute_diff(ideal, cart, top_up=top_up)
    if missing.empty:
        st.success("Ingenting mangler — kurven dekker hele listen.")
        return

    search = st.text_input(
        "🔍 Søk",
        placeholder="Filtrer på varetype, produkt eller kategori",
        key="diff_search",
    )
    if search:
        missing = _filter(missing, search,
                          ["key", "product_name", "category"])
    if missing.empty:
        st.info("Ingen treff.")
        return

    view = missing[
        ["category", "key", "product_name", "foreslått_antall",
         "i_kurv", "mangler", "median_days"]
    ].copy()
    view["median_days"] = view["median_days"].apply(
        lambda d: f"hver {int(round(d))}. d"
    )
    view.columns = ["Kategori", "Varetype", "Produkt", "Forslag",
                    "I kurv", "Mangler", "Kadens"]
    edited = st.data_editor(
        view,
        width="stretch",
        hide_index=True,
        column_config={
            "Mangler": st.column_config.NumberColumn(
                min_value=0, step=1, format="%d",
                help="Sett til 0 for å droppe varen",
            ),
        },
        disabled=["Kategori", "Varetype", "Produkt", "Forslag",
                  "I kurv", "Kadens"],
        key="diff_editor",
    )

    st.divider()
    col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
    title = col1.text_input(
        "Listetittel",
        value="Resterende — ukehandel",
        key="diff_title",
    )
    if col2.button("📤 Opprett mangelliste på Oda", type="primary",
                   key="diff_create", width="stretch"):
        items = [
            (int(missing.iloc[i]["product_id"]),
             int(edited.iloc[i]["Mangler"]))
            for i in range(len(missing))
            if int(edited.iloc[i]["Mangler"]) > 0
        ]
        if not items:
            st.warning("Ingen varer å legge til (alle på 0).")
            return
        with st.spinner("Oppretter …"):
            client = build_client()
            result = create_list(
                client, title,
                "Diff mellom faste varer og handlekurv",
            )
            if not result:
                st.error("Kunne ikke opprette listen.")
                return
            list_id = result["id"]
            ok = add_products(client, list_id, items)
            st.success(
                f"La til {ok}/{len(items)} varer. "
                f"[Åpne listen](https://oda.com/no/account/lists/details/{list_id}/)"
            )


def page_restock():
    st.header("🔁 Restock — hva forfaller?")

    horizon = st.slider(
        "Horisont (dager)", 3, 30, 14,
        help=(
            "Hvor langt fremover du vil se. Viser varer som snart går "
            "tomt, basert på hvor ofte du pleier å kjøpe dem."
        ),
    )
    show_all = st.checkbox(
        "Vis alle (også 'i rute')", value=False,
        help="Ta med varer det fortsatt er en stund til du trenger.",
    )
    by_type = st.checkbox(
        "Grupper per varetype (anbefalt)", value=True,
        help=(
            "Behandler alle melk som én vare, uansett merke. Slå av "
            "hvis du vil se hvert enkelt produkt for seg."
        ),
    )

    cadence = compute_cadence(lines, by_type=by_type)
    if cadence.empty:
        st.warning("Ingen kadensdata.")
        return

    view_df = cadence if show_all else cadence[
        cadence["days_until_due"] <= horizon
    ]
    if view_df.empty:
        st.success(f"Ingenting forfaller innen {horizon} dager.")
        return

    search = st.text_input(
        "🔍 Søk",
        placeholder="Filtrer på varetype, produkt eller kategori",
        key="restock_search",
    )
    if search:
        view_df = _filter(view_df, search,
                          ["key", "product_name", "category"])
    if view_df.empty:
        st.info("Ingen treff.")
        return

    v = view_df.copy()
    v["status"] = v["status"].map({
        "forfalt": "🔴 forfalt",
        "akkurat nå": "🟡 akkurat nå",
        "snart": "🔵 snart",
        "i rute": "🟢 i rute",
    })
    v["median_days"] = v["median_days"].apply(
        lambda d: f"{int(round(d))} d"
    )
    v["days_until_due"] = v["days_until_due"].apply(
        lambda d: "i dag" if d == 0
        else (f"{-int(d)} d siden" if d < 0 else f"om {int(d)} d")
    )
    v["last"] = v["last"].dt.date
    v = v[["key", "product_name", "category", "n_buys", "last",
           "median_days", "days_until_due", "status"]]
    v.columns = ["Varetype", "Eksempel", "Kategori", "Kjøp",
                 "Sist", "Kadens", "Forfaller", "Status"]
    st.dataframe(v, width="stretch", hide_index=True)

    n_overdue = int((cadence["status"] == "forfalt").sum())
    n_now = int((cadence["status"] == "akkurat nå").sum())
    n_soon = int((cadence["status"] == "snart").sum())
    st.caption(
        f"Totalt: {n_overdue} forfalt · {n_now} akkurat nå · "
        f"{n_soon} snart · {len(cadence)} faste varetyper analysert"
    )


pages = [
    st.Page(page_handleliste, title="Handleliste", icon="📋",
            url_path="handleliste", default=True),
    st.Page(page_diff, title="Diff mot kurv", icon="🛒",
            url_path="diff"),
    st.Page(page_restock, title="Restock", icon="🔁",
            url_path="restock"),
]
st.navigation(pages).run()
