"""Tester for parse_delivery_time — Odas datostrengs-varianter."""

from __future__ import annotations

import pandas as pd

from min_oda.fetch_orders import parse_delivery_time


def test_numeric_day_month():
    """Standardformat fra Oda: 'fre 8. mai, 10:08' + månedsetikett."""
    out = parse_delivery_time("fre 8. mai, 10:08", "Mai")
    assert out is not None
    assert out.month == 5
    assert out.day == 8


def test_month_label_with_year_overrides_current_year():
    """Eldre ordrer har 'November 2025' som månedsetikett — året må brukes
    så vi ikke datostempler dem som inneværende år."""
    out = parse_delivery_time("ons 12. november, 09:30", "November 2025")
    assert out.year == 2025
    assert out.month == 11
    assert out.day == 12


def test_relative_today():
    """'i dag' skal tolkes som dagens dato (Oslo). Uten denne håndteringen
    droppes dagens ordre fra orders.csv fordi den numeriske regexen ikke
    treffer."""
    out = parse_delivery_time("i dag, 09:10", "Mai")
    assert out is not None
    today = pd.Timestamp.now(tz="Europe/Oslo").normalize()
    assert out == today


def test_relative_yesterday():
    out = parse_delivery_time("i går, 18:45", "Mai")
    assert out is not None
    expected = pd.Timestamp.now(tz="Europe/Oslo").normalize() - pd.Timedelta(days=1)
    assert out == expected


def test_unparseable_returns_none():
    """Tomme eller helt ukjente strenger gir None — ikke krasj."""
    assert parse_delivery_time(None, "Mai") is None
    assert parse_delivery_time("", "Mai") is None
    assert parse_delivery_time("ingenting gjenkjennelig", None) is None
