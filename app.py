"""Streamlit-GUI for Oda-analyse.

Kjør:  uv run streamlit run app.py
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
cycle = st.sidebar.slider("Listesyklus (dager)", 7, 28, 14, step=1,
                          help="Antall = ceil(syklus / median-intervall)")
top_n = st.sidebar.slider("Maks varetyper på liste", 20, 120, 40, step=5)
max_per_cat = st.sidebar.slider("Maks per kategori", 4, 20, 8)
st.sidebar.caption(
    f"{len(lines)} linjer · {lines['order_id'].nunique()} ordrer"
)

if st.sidebar.button("🔄 Tøm cache"):
    st.cache_data.clear()
    st.rerun()

tab_list, tab_diff, tab_restock = st.tabs(
    ["📋 Handleliste", "🛒 Diff mot kurv", "🔁 Restock"]
)


with tab_list:
    st.header("Foreslått handleliste")
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
    else:
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
        st.dataframe(view, width="stretch", hide_index=True)

        st.divider()
        col1, col2 = st.columns([3, 1])
        title = col1.text_input("Listetittel", value="Ukehandel — familien")
        if col2.button("📤 Opprett liste på Oda", type="primary",
                       width="stretch"):
            with st.spinner("Oppretter …"):
                client = build_client()
                result = create_list(
                    client, title, f"Faste varer · {cycle} d syklus"
                )
                if not result:
                    st.error("Kunne ikke opprette listen.")
                else:
                    list_id = result["id"]
                    items = [
                        (int(r["product_id"]), int(r["foreslått_antall"]))
                        for _, r in ideal.iterrows()
                    ]
                    ok = add_products(client, list_id, items)
                    st.success(
                        f"La til {ok}/{len(items)} varer. "
                        f"[Åpne listen](https://oda.com/no/account/lists/details/{list_id}/)"
                    )


with tab_diff:
    st.header("Diff mot handlekurv")

    top_up = st.checkbox(
        "Inkluder også varer som er i kurv, men i for lavt antall",
        value=False,
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
    else:
        view = missing[
            ["category", "key", "product_name", "foreslått_antall",
             "i_kurv", "mangler", "median_days"]
        ].copy()
        view["median_days"] = view["median_days"].apply(
            lambda d: f"hver {int(round(d))}. d"
        )
        view.columns = ["Kategori", "Varetype", "Produkt", "Forslag",
                        "I kurv", "Mangler", "Kadens"]
        st.dataframe(view, width="stretch", hide_index=True)

        st.divider()
        col1, col2 = st.columns([3, 1])
        title2 = col1.text_input(
            "Listetittel",
            value="Resterende — ukehandel",
            key="diff_title",
        )
        if col2.button("📤 Opprett mangelliste på Oda", type="primary",
                       key="diff_create", width="stretch"):
            with st.spinner("Oppretter …"):
                client = build_client()
                result = create_list(
                    client, title2,
                    "Diff mellom faste varer og handlekurv",
                )
                if not result:
                    st.error("Kunne ikke opprette listen.")
                else:
                    list_id = result["id"]
                    items = [
                        (int(r["product_id"]), int(r["mangler"]))
                        for _, r in missing.iterrows()
                    ]
                    ok = add_products(client, list_id, items)
                    st.success(
                        f"La til {ok}/{len(items)} varer. "
                        f"[Åpne listen](https://oda.com/no/account/lists/details/{list_id}/)"
                    )


with tab_restock:
    st.header("Restock — hva forfaller?")

    horizon = st.slider("Horisont (dager)", 3, 30, 14)
    show_all = st.checkbox("Vis alle (også 'i rute')", value=False)
    by_type = st.checkbox("Grupper per varetype (anbefalt)", value=True)

    cadence = compute_cadence(lines, by_type=by_type)
    if cadence.empty:
        st.warning("Ingen kadensdata.")
    else:
        view_df = cadence if show_all else cadence[
            cadence["days_until_due"] <= horizon
        ]
        if view_df.empty:
            st.success(f"Ingenting forfaller innen {horizon} dager.")
        else:
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
