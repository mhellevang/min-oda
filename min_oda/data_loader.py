"""Shared data loading helpers for all analysis scripts.

Eliminates the duplicated CSV-loading / date-parsing / coercion blocks that
appeared in nearly every script.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"


def load_orders() -> pd.DataFrame:
    """Load orders.csv with standard cleaning.

    Returns a DataFrame with:
        - date  (UTC-aware Timestamp)
        - total (numeric)
    """
    orders = pd.read_csv(DATA_DIR / "orders.csv")
    orders["date"] = pd.to_datetime(orders["date"], utc=True, errors="coerce")
    orders["total"] = pd.to_numeric(orders["total"], errors="coerce")
    return orders


def load_lines(orders: pd.DataFrame | None = None) -> pd.DataFrame:
    """Load lines.csv with standard cleaning.

    If *orders* is provided, a 'date' column is joined from
    ``orders.set_index("order_number")["date"]``.

    Returns a DataFrame with:
        - unit_price   (numeric, coerced)
        - line_total   (numeric, coerced)
        - quantity     (numeric, NaN → 1)
        - vat_pct      (float, if 'vat_percentage' exists)
        - year         (int, if 'date' joined)
        - month_num    (int, if 'date' joined)
    """
    lines = pd.read_csv(DATA_DIR / "lines.csv")

    if orders is not None:
        orders_idx = orders.set_index("order_number")["date"]
        lines["date"] = pd.to_datetime(lines["order_id"].map(orders_idx), utc=True)

    for col in ("unit_price", "line_total"):
        lines[col] = pd.to_numeric(lines[col], errors="coerce")

    lines["quantity"] = pd.to_numeric(lines["quantity"], errors="coerce").fillna(1)
    lines["vat_pct"] = lines["vat_percentage"].str.rstrip("%").astype(float)

    if "date" in lines.columns and lines["date"].notna().any():
        lines["year"] = lines["date"].dt.year
        lines["month_num"] = lines["date"].dt.month

    return lines


def load_both() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience: load orders and joined lines in one call."""
    orders = load_orders()
    lines = load_lines(orders)
    return orders, lines
