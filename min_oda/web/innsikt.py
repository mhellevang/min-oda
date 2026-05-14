"""Compute-funksjoner for /innsikt-fanen.

Erstatter de gamle report.py / portrait.py / seasonality.py / basket.py /
analyze.py-scriptene. Ingen rich/terminal-output — bare ren data inn,
dicts/lister ut, slik at Jinja-templaten kan rendre HTML.
"""

from __future__ import annotations

import base64
import io
from collections import Counter
from itertools import combinations

import matplotlib

matplotlib.use("Agg")  # noqa: E402  (sett før pyplot-import)

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


NB_MONTHS = ["", "jan", "feb", "mar", "apr", "mai", "jun",
             "jul", "aug", "sep", "okt", "nov", "des"]


def _kw(s: pd.Series, pattern: str) -> pd.Series:
    return s.fillna("").str.contains(pattern, case=False, regex=True)


# ---------- nøkkeltall --------------------------------------------------


def kpis(orders: pd.DataFrame, lines: pd.DataFrame) -> dict:
    if orders.empty:
        return {
            "n_orders": 0, "total_kr": 0, "avg_kr": 0,
            "date_min": None, "date_max": None,
            "freq_per_week": 0, "n_unique_products": 0, "n_lines": 0,
        }
    n_orders = len(orders)
    total = float(orders["total"].sum(skipna=True))
    avg = float(orders["total"].mean(skipna=True))
    date_min = orders["date"].min()
    date_max = orders["date"].max()
    n_days = (date_max - date_min).days if pd.notna(date_min) and pd.notna(date_max) else 0
    freq = n_orders * 7 / n_days if n_days else 0
    return {
        "n_orders": n_orders,
        "total_kr": total,
        "avg_kr": avg,
        "date_min": date_min,
        "date_max": date_max,
        "freq_per_week": freq,
        "n_unique_products": int(lines["product_name"].nunique()) if not lines.empty else 0,
        "n_lines": len(lines),
    }


# ---------- plot --------------------------------------------------------


def monthly_spend_plot_b64(orders: pd.DataFrame) -> str | None:
    df = orders.dropna(subset=["date"]).copy()
    if df.empty:
        return None
    df["month"] = (
        df["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("M")
    )
    monthly = df.groupby("month")["total"].sum()
    full_idx = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
    monthly = monthly.reindex(full_idx, fill_value=0)

    fig, ax = plt.subplots(figsize=(13, 4.5))
    ax.bar(monthly.index.to_timestamp(), monthly.values, width=25, color="#2a6f97")
    yr_min = monthly.index.min().year
    yr_max = monthly.index.max().year
    ax.set_title(f"Månedlig forbruk ({yr_min}–{yr_max})")
    ax.set_ylabel("Sum kr")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------- topp produkter/kategorier ----------------------------------


def top_products(lines: pd.DataFrame, months: int = 12, n: int = 15) -> list[dict]:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=months * 30)
    recent = lines[lines["date"] >= cutoff]
    df = (
        recent.dropna(subset=["product_name"])
        .groupby("product_name")
        .agg(ganger=("order_id", "nunique"), sum_kr=("line_total", "sum"))
        .sort_values("ganger", ascending=False)
        .head(n)
        .reset_index()
    )
    if df.empty:
        return []
    max_g = int(df["ganger"].max())
    return [
        {
            "name": str(r["product_name"]),
            "ganger": int(r["ganger"]),
            "sum_kr": float(r["sum_kr"]),
            "bar_pct": int(r["ganger"] / max_g * 100),
        }
        for _, r in df.iterrows()
    ]


def top_categories(lines: pd.DataFrame, months: int = 12, n: int = 10) -> list[dict]:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=months * 30)
    recent = lines[lines["date"] >= cutoff]
    df = (
        recent.dropna(subset=["category"])
        .groupby("category")
        .agg(linjer=("product_name", "count"), sum_kr=("line_total", "sum"))
        .sort_values("sum_kr", ascending=False)
        .head(n)
        .reset_index()
    )
    if df.empty:
        return []
    max_kr = float(df["sum_kr"].max())
    return [
        {
            "name": str(r["category"]),
            "linjer": int(r["linjer"]),
            "sum_kr": float(r["sum_kr"]),
            "bar_pct": int(r["sum_kr"] / max_kr * 100),
        }
        for _, r in df.iterrows()
    ]


# ---------- husstandens DNA (staples) ---------------------------------


def staples(orders: pd.DataFrame, lines: pd.DataFrame, threshold_pct: float = 0.4, n: int = 15) -> list[dict]:
    n_orders = len(orders)
    if n_orders == 0:
        return []
    threshold = n_orders * threshold_pct
    counts = (
        lines.dropna(subset=["product_name"])
        .groupby("product_name")["order_id"]
        .nunique()
        .sort_values(ascending=False)
    )
    sticky = counts[counts >= threshold].head(n)
    return [
        {"name": str(name), "count": int(c), "pct": int(c / n_orders * 100)}
        for name, c in sticky.items()
    ]


# ---------- matkultur ---------------------------------------------------


CUISINES = {
    "Norsk tradisjon": r"jarlsberg|gilde|tine|kavli|brunost|fiskekake|kjøttkake|leverpostei|nugatti|prim|geitost|stange",
    "Italiensk": r"pasta|spagetti|pesto|parmesan|mozzarella|pizza|tomatpuré|risotto|prosciutto",
    "Meksikansk/Tex-Mex": r"tortilla|salsa|taco|nachos|guacamole|jalape|fajita|chili",
    "Asiatisk": r"soya|nudler|ramen|wok|sesam|kokosmelk|curry|sushi|tofu|ingefær",
    "Indisk": r"naan|tandoori|garam|tikka|masala|paneer|chutney",
}


def cuisine_mix(lines: pd.DataFrame) -> list[dict]:
    total_spend = float(lines["line_total"].sum())
    if total_spend == 0:
        return []
    rows = []
    for name, pat in CUISINES.items():
        m = lines[_kw(lines["product_name"], pat)]
        s = float(m["line_total"].sum())
        rows.append({
            "name": name,
            "linjer": len(m),
            "sum_kr": s,
            "pct": s / total_spend * 100,
        })
    rows.sort(key=lambda r: -r["sum_kr"])
    return rows


# ---------- drikke ------------------------------------------------------


BEVERAGE_PATTERNS = {
    "Brus":
        r"pepsi|coca[-\s]?cola|coke\b|solo\b|farris|julebrus|sprite|fanta|7up|urge|battery|red\s*bull",
    "Øl (alkoholholdig)":
        r"peroni|frydenlund(?!.*alkoholfri)|schous\s+pilsner|sol\s+flaske|nøisom|"
        r"hansa(?!.*alkoholfri)|aass(?!.*alkoholfri)|ringnes\s+pils|tuborg|"
        r"carlsberg(?!.*alkoholfri)|heineken(?!.*alkoholfri)|mack\s+pils|"
        r"corona\s+extra|stella\s+artois|grans|nøgne|pale\s+ale|\bipa\b(?!.*alkoholfri)",
    "Øl (alkoholfri)":
        r"clausthaler|munkholm|no\s+worries|alkoholfri",
    "Juice/smoothie":
        r"appelsinjuice|eplejuice|tranebær|smoothie|froosh|juice\b|tropicana",
    "Kaffe":
        r"kaffe|kaffebønner|espresso|filtermalt|kaffekapsler|kapsler",
    "Vann":
        r"snåsavann|imsdal|farris\s+naturell|kildevann|mineralvann",
}


def beverages(lines: pd.DataFrame) -> list[dict]:
    seen_alkoholfri: set[int] = set()
    rows = []
    for label, pat in BEVERAGE_PATTERNS.items():
        m = lines[_kw(lines["product_name"], pat)]
        if "alkoholfri" in label.lower():
            seen_alkoholfri.update(m.index)
        elif "alkoholholdig" in label.lower():
            m = m[~m.index.isin(seen_alkoholfri)]
        rows.append({
            "name": label,
            "linjer": len(m),
            "enheter": int(m["quantity"].sum()) if not m.empty else 0,
            "sum_kr": float(m["line_total"].sum()) if not m.empty else 0,
        })
    rows.sort(key=lambda r: -r["sum_kr"])
    return rows


# ---------- kokestil + prisbevissthet + helse -------------------------


def cooking_style(lines: pd.DataFrame) -> dict:
    raw_pat = r"agurk|tomat|paprika|løk|hvitløk|gulrot|potet|kjøttdeig|karbonadedeig|kylling|laks|torsk|egg\s|mel\s|ris\s|pasta\s"
    conv_pat = r"ferdig|grandiosa|fjordland|toro\s|knorr|stabbur|frosset.*middag|pizza"
    raw_m = lines[_kw(lines["product_name"], raw_pat)]
    conv_m = lines[_kw(lines["product_name"], conv_pat)]
    total = float(lines["line_total"].sum())
    return {
        "raw_linjer": len(raw_m),
        "raw_kr": float(raw_m["line_total"].sum()),
        "raw_pct": float(raw_m["line_total"].sum()) / total * 100 if total else 0,
        "conv_linjer": len(conv_m),
        "conv_kr": float(conv_m["line_total"].sum()),
        "conv_pct": float(conv_m["line_total"].sum()) / total * 100 if total else 0,
    }


def price_consciousness(lines: pd.DataFrame) -> dict:
    cheap_pat = r"vår laveste pris|first\s*price|eldorado|x-tra|coop\s*x"
    eco_pat = r"økologisk|øko\s"
    cheap = lines[_kw(lines["product_name"], cheap_pat)]
    eco = lines[_kw(lines["product_name"], eco_pat)]
    total = float(lines["line_total"].sum())
    return {
        "cheap_linjer": len(cheap),
        "cheap_kr": float(cheap["line_total"].sum()),
        "cheap_pct": float(cheap["line_total"].sum()) / total * 100 if total else 0,
        "eco_linjer": len(eco),
        "eco_pct": len(eco) / len(lines) * 100 if not lines.empty else 0,
    }


def health(lines: pd.DataFrame) -> dict:
    veg = lines[lines["category"].fillna("") == "Frukt og grønt"]
    meat = lines[lines["category"].fillna("").isin(["Kylling og kjøtt", "Grill"])]
    fish = lines[lines["category"].fillna("") == "Fisk og sjømat"]
    sweets = lines[lines["category"].fillna("").isin(
        ["Sjokolade, snacks og godteri", "Iskrem, dessert og kjeks"]
    )]
    total = float(lines["line_total"].sum())

    def stats(sub: pd.DataFrame) -> dict:
        s = float(sub["line_total"].sum())
        return {
            "linjer": len(sub),
            "sum_kr": s,
            "pct": s / total * 100 if total else 0,
        }

    meat_kr = float(meat["line_total"].sum())
    fish_kr = float(fish["line_total"].sum())
    return {
        "veg": stats(veg),
        "meat": stats(meat),
        "fish": stats(fish),
        "sweets": stats(sweets),
        "meat_fish_ratio": meat_kr / fish_kr if fish_kr > 0 else None,
    }


# ---------- baby/livshendelser ----------------------------------------


def baby_signal(lines: pd.DataFrame) -> dict | None:
    baby_pat = (
        r"bleier|stellekluter|våtservietter\s+baby|morsmelk|nan\s+1|nan\s+pro|"
        r"barnegrøt|tåteflaske"
    )
    baby = lines[_kw(lines["product_name"], baby_pat)].dropna(subset=["date"])
    if baby.empty:
        return None
    return {
        "first": baby["date"].min(),
        "last": baby["date"].max(),
        "n_lines": len(baby),
        "sum_kr": float(baby["line_total"].sum()),
    }


# ---------- sesongprodukter -------------------------------------------


def seasonal_products(lines: pd.DataFrame, n: int = 20) -> list[dict]:
    df = lines.dropna(subset=["product_name", "date"]).copy()
    df["month_num"] = df["date"].dt.month
    rows = []
    for prod, sub in df.groupby("product_name"):
        total = len(sub)
        if total < 3:
            continue
        monthcount = sub["month_num"].value_counts().to_dict()
        if len(monthcount) > 3:
            continue
        low = str(prod).lower()
        if any(k in low for k in ["bleie", "morsmelk", "nan "]):
            continue
        peak_months = sorted(monthcount.keys())
        rows.append({
            "name": str(prod),
            "ganger": total,
            "months": ", ".join(NB_MONTHS[int(m)] for m in peak_months),
        })
    rows.sort(key=lambda r: -r["ganger"])
    return rows[:n]


# ---------- sommerferie-gapet -----------------------------------------


def july_gap(orders: pd.DataFrame) -> dict:
    df = orders.dropna(subset=["date"]).copy()
    df["month_num"] = df["date"].dt.month
    df["year"] = df["date"].dt.year
    by_month = df.groupby("month_num").agg(
        ordrer=("order_number", "count"),
        sum_kr=("total", "sum"),
    )
    n_years = df["year"].nunique()

    rows = []
    for m in range(1, 13):
        if m in by_month.index:
            r = by_month.loc[m]
            rows.append({
                "month": NB_MONTHS[m],
                "ordrer": int(r["ordrer"]),
                "sum_kr": float(r["sum_kr"]),
            })
        else:
            rows.append({"month": NB_MONTHS[m], "ordrer": 0, "sum_kr": 0})
    return {"rows": rows, "n_years": n_years}


# ---------- basket-analyse --------------------------------------------


def basket_pairs(
    lines: pd.DataFrame, min_orders: int = 6, min_pair: int = 3
) -> tuple[pd.DataFrame, dict[int, int], dict[int, str], int]:
    lines = lines.dropna(subset=["product_id", "order_id"]).copy()
    lines["product_id"] = lines["product_id"].astype(int)
    name_map: dict[int, str] = (
        lines.dropna(subset=["product_name"])
        .groupby("product_id")["product_name"]
        .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iat[0])
        .to_dict()
    )

    baskets = lines.groupby("order_id")["product_id"].apply(set).tolist()
    n_orders = len(baskets)

    prod_count: Counter[int] = Counter()
    for b in baskets:
        prod_count.update(b)

    frequent = {p for p, c in prod_count.items() if c >= min_orders}

    pair_count: Counter[tuple[int, int]] = Counter()
    for b in baskets:
        f = sorted(b & frequent)
        for a, c in combinations(f, 2):
            pair_count[(a, c)] += 1

    rows = []
    for (a, b), c in pair_count.items():
        if c < min_pair:
            continue
        pa, pb = prod_count[a], prod_count[b]
        rows.append({
            "a": a, "b": b, "co": c,
            "support": c / n_orders,
            "conf_a_to_b": c / pa,
            "conf_b_to_a": c / pb,
            "lift": (c * n_orders) / (pa * pb),
        })

    return pd.DataFrame(rows), dict(prod_count), name_map, n_orders


def _pair_to_dict(r: pd.Series, name_map: dict[int, str]) -> dict:
    return {
        "a": str(name_map.get(int(r["a"]), f"#{int(r['a'])}")),
        "b": str(name_map.get(int(r["b"]), f"#{int(r['b'])}")),
        "co": int(r["co"]),
        "lift": float(r["lift"]),
        "support_pct": float(r["support"]) * 100,
    }


def top_lift_pairs(pairs: pd.DataFrame, name_map: dict, n: int = 12) -> list[dict]:
    if pairs.empty:
        return []
    return [_pair_to_dict(r, name_map) for _, r in pairs.sort_values("lift", ascending=False).head(n).iterrows()]


def top_support_pairs(pairs: pd.DataFrame, name_map: dict, n: int = 12) -> list[dict]:
    if pairs.empty:
        return []
    return [_pair_to_dict(r, name_map) for _, r in pairs.sort_values("support", ascending=False).head(n).iterrows()]


def basket_for_product(
    pairs: pd.DataFrame,
    name_map: dict[int, str],
    counts: dict[int, int],
    n_orders: int,
    query: str,
    n: int = 12,
) -> dict | None:
    if pairs.empty or not query.strip():
        return None
    matches = [pid for pid, nm in name_map.items() if query.lower() in nm.lower()]
    if not matches:
        return {"name": None, "query": query, "companions": [], "other_matches": []}

    matches.sort(key=lambda p: counts.get(p, 0), reverse=True)
    pid = matches[0]
    base_count = counts.get(pid, 0)

    related = pairs[(pairs["a"] == pid) | (pairs["b"] == pid)].copy()
    if related.empty:
        return {
            "name": name_map[pid], "query": query,
            "base_count": base_count, "n_orders": n_orders,
            "companions": [],
            "other_matches": [name_map[p] for p in matches[1:5]],
        }
    related["other"] = related.apply(
        lambda r: int(r["b"]) if int(r["a"]) == pid else int(r["a"]), axis=1
    )
    related["conf_to_other"] = related.apply(
        lambda r: r["conf_a_to_b"] if int(r["a"]) == pid else r["conf_b_to_a"], axis=1,
    )
    top = related.sort_values("conf_to_other", ascending=False).head(n)
    return {
        "name": name_map[pid], "query": query,
        "base_count": base_count, "n_orders": n_orders,
        "companions": [
            {
                "name": str(name_map.get(int(r["other"]), f"#{int(r['other'])}")),
                "co": int(r["co"]),
                "conf_pct": float(r["conf_to_other"]) * 100,
                "lift": float(r["lift"]),
            }
            for _, r in top.iterrows()
        ],
        "other_matches": [name_map[p] for p in matches[1:5]],
    }
