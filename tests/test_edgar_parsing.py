"""Tests du parsing EDGAR sur une fixture locale — aucun appel reseau."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from investassist.config import load_settings
from investassist.fundamentals import split_factor_after
from investassist.providers.edgar import EdgarClient, fiscal_year_of

FIXTURE = Path(__file__).parent / "fixtures" / "companyfacts_testco.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    settings = load_settings()
    object.__setattr__(settings, "cache_dir", tmp_path / "cache")
    object.__setattr__(settings, "cache_ttl_hours", 0)
    edgar = EdgarClient(settings)
    facts = json.loads(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(edgar, "ticker_to_cik", lambda ticker: "0001234567")
    monkeypatch.setattr(edgar, "company_facts", lambda cik: facts)
    return edgar


# ------------------------------------------------- convention d'exercice
@pytest.mark.parametrize(
    "period_end,expected",
    [
        (date(2025, 12, 31), 2025),
        (date(2025, 6, 30), 2025),   # cloture en juin -> exercice 2025
        (date(2025, 1, 31), 2024),   # cloture fin janvier -> exercice 2024
        (date(2025, 5, 31), 2024),
    ],
)
def test_convention_exercice(period_end, expected):
    assert fiscal_year_of(period_end) == expected


# ------------------------------------------------------------- parsing
def test_serie_annuelle_complete(client):
    records, warnings = client.annual_records("TEST")
    assert warnings == []
    assert [r.fiscal_year for r in records] == [2021, 2022, 2023, 2024, 2025]
    assert records[-1].period_end == date(2025, 12, 31)


def test_periodes_trimestrielles_ignorees(client):
    """Une periode de 3 mois ne doit jamais etre lue comme un exercice."""
    records, _ = client.annual_records("TEST")
    values = {r.fiscal_year: r.get("revenue") for r in records}
    assert values[2025] == 2400.0  # et non 700.0 (le trimestre)


def test_retraitement_ecrase_la_publication_initiale(client):
    """A exercice egal, la publication la plus recente doit primer."""
    records, _ = client.annual_records("TEST")
    values = {r.fiscal_year: r.get("revenue") for r in records}
    assert values[2025] == 2400.0  # depot 2026-02-01, et non 2350.0 du 2026-01-15


def test_ebitda_reconstitue(client):
    records, _ = client.annual_records("TEST")
    latest = records[-1]
    # EBITDA = resultat operationnel + amortissements = 500 + 80
    assert latest.get("ebitda") == pytest.approx(580.0)


def test_dette_et_tresorerie(client):
    records, _ = client.annual_records("TEST")
    latest = records[-1]
    assert latest.get("total_debt") == pytest.approx(420.0)
    assert latest.get("cash") == pytest.approx(500.0)
    assert latest.get("current_assets") / latest.get("current_liabilities") == pytest.approx(1100 / 600)


def test_dates_de_depot_conservees_pour_les_donnees_par_action(client):
    records, _ = client.annual_records("TEST")
    filed = {r.fiscal_year: r.filed.get("eps_diluted") for r in records}
    assert filed[2021] == "2022-02-01"
    assert filed[2025] == "2026-02-01"


def test_titre_hors_registre_sec(client, monkeypatch):
    monkeypatch.setattr(client, "ticker_to_cik", lambda ticker: None)
    records, warnings = client.annual_records("AIR.PA")
    assert records == []
    assert "absent du registre SEC" in warnings[0]


# ------------------------------------------- divisions d'actions
def test_facteur_split_selon_la_date_de_depot():
    """Le piege central : une donnee deposee APRES la division est deja
    retraitee et ne doit pas l'etre une seconde fois."""
    splits = {"2024-06-01": 4.0}
    # Depot anterieur a la division -> a retraiter
    assert split_factor_after(date(2022, 2, 1), splits) == pytest.approx(4.0)
    # Depot posterieur -> deja sur la base actuelle
    assert split_factor_after(date(2025, 2, 1), splits) == pytest.approx(1.0)


def test_facteur_split_cumulatif():
    splits = {"2021-07-20": 4.0, "2024-06-10": 10.0}
    assert split_factor_after(date(2020, 1, 1), splits) == pytest.approx(40.0)
    assert split_factor_after(date(2022, 1, 1), splits) == pytest.approx(10.0)
    assert split_factor_after(date(2026, 1, 1), splits) == pytest.approx(1.0)


def test_facteur_split_sans_reference():
    assert split_factor_after(None, {"2024-06-01": 4.0}) == 1.0
    assert split_factor_after(date(2020, 1, 1), {}) == 1.0
