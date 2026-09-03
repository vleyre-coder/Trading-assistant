"""Tests du lecteur de depots europeens ESEF, sans aucun appel reseau.

Les faits ci-dessous reproduisent la structure exacte d'un fichier
filings.xbrl.org, relevee sur le depot 2024 de LVMH.
"""
from __future__ import annotations

from datetime import date

import pytest

from investassist.providers import esef
from investassist.providers.esef import EsefClient


def fait(concept: str, periode: str, valeur, **axes):
    dimensions = {
        "concept": concept,
        "entity": "scheme:IOG4E947OATN0KJYSD45",
        "period": periode,
        "unit": "iso4217:EUR",
    }
    dimensions.update(axes)
    return {"value": valeur, "decimals": -6, "dimensions": dimensions}


def exercice(annee: int) -> str:
    return f"{annee}-01-01T00:00:00/{annee + 1}-01-01T00:00:00"


DEPOT = {
    "documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
    "facts": {
        "f1": fait("ifrs-full:Revenue", exercice(2024), "84683000000.0"),
        "f2": fait("ifrs-full:Revenue", exercice(2023), "86153000000.0"),
        "f3": fait("ifrs-full:Revenue", exercice(2022), "79184000000.0"),
        "f4": fait("ifrs-full:ProfitLossAttributableToOwnersOfParent", exercice(2024), "12550000000.0"),
        "f5": fait("ifrs-full:GrossProfit", exercice(2024), "56765000000.0"),
        # Solde de bilan date au premier jour de l'exercice suivant :
        # convention frequente, qui doit se rattacher a l'exercice 2024.
        "f6": fait("ifrs-full:Equity", "2025-01-01T00:00:00", "69287000000.0"),
        "f7": fait("ifrs-full:Assets", "2025-01-01T00:00:00", "149190000000.0"),
        "f8": fait("ifrs-full:LongtermBorrowings", "2025-01-01T00:00:00", "12091000000.0"),
        "f9": fait(
            "ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
            "2025-01-01T00:00:00", "10851000000.0",
        ),
        # Piege 1 : ventilation par composante de capitaux propres. Valeur
        # PARTIELLE, qui ne doit jamais etre prise pour le total consolide.
        "f10": fait(
            "ifrs-full:Equity", "2025-01-01T00:00:00", "152000000.0",
            **{"ifrs-full:ComponentsOfEquityAxis": "ifrs-full:IssuedCapitalMember"},
        ),
        # Piege 2 : periode trimestrielle, a ecarter d'un historique annuel.
        "f11": fait("ifrs-full:Revenue", "2024-01-01T00:00:00/2024-04-01T00:00:00", "20000000000.0"),
    },
}


@pytest.fixture()
def client(tmp_path):
    from investassist.config import load_settings

    reglages = load_settings()
    objet = EsefClient.__new__(EsefClient)
    objet.settings = reglages
    objet.filers = {"MC.PA": "LVMH MOET HENNESSY LOUIS VUITTON"}
    objet.cache = _CacheMuet()
    objet.session = None
    objet.limiter = None
    return objet


class _CacheMuet:
    def get(self, *_):
        return None

    def set(self, *_):
        return None


def test_exercice_d_instant_reconcilie_les_deux_conventions():
    """Un bilan de cloture 2024 est date soit 31/12/2024 soit 01/01/2025
    selon l'emetteur : les deux doivent tomber sur l'exercice 2024."""
    assert esef.exercice_d_instant(date(2025, 1, 1)) == 2024
    assert esef.exercice_d_instant(date(2024, 12, 31)) == 2024


def test_reduction_ecarte_les_ventilations_et_ne_garde_que_l_utile():
    reduit = EsefClient._reduire(DEPOT)
    concepts = [f["concept"] for f in reduit["faits"]]
    # Le fait ventile par composante de capitaux propres doit avoir disparu :
    # deux faits « Equity » subsistaient sinon, dont un a 152 M au lieu de 69 Md.
    equity = [f for f in reduit["faits"] if f["concept"] == "ifrs-full:Equity"]
    assert len(equity) == 1
    assert float(equity[0]["value"]) == pytest.approx(69_287_000_000.0)
    assert "ifrs-full:Assets" in concepts


def test_lecture_d_un_depot(client, monkeypatch):
    monkeypatch.setattr(client, "_faits", lambda chemin: EsefClient._reduire(DEPOT))
    monkeypatch.setattr(
        client, "depots", lambda deposant: [(date(2024, 12, 31), "/faux.json")]
    )
    records, avertissements = client.annual_records("MC.PA")
    assert avertissements == []

    par_annee = {r.fiscal_year: r for r in records}
    # Trois exercices tires d'un seul depot : c'est tout l'interet de la source.
    assert sorted(par_annee) == [2022, 2023, 2024]
    assert par_annee[2024].get("revenue") == pytest.approx(84_683_000_000.0)
    assert par_annee[2024].get("net_income") == pytest.approx(12_550_000_000.0)
    assert par_annee[2024].get("gross_profit") == pytest.approx(56_765_000_000.0)
    assert par_annee[2024].get("equity") == pytest.approx(69_287_000_000.0)
    # Dette totale = emprunts longs + part courante.
    assert par_annee[2024].get("total_debt") == pytest.approx(22_942_000_000.0)


def test_periode_trimestrielle_ignoree(client, monkeypatch):
    """Un chiffre d'affaires trimestriel ne doit jamais entrer dans un
    historique annuel : il ecraserait l'exercice complet."""
    monkeypatch.setattr(client, "_faits", lambda chemin: EsefClient._reduire(DEPOT))
    monkeypatch.setattr(
        client, "depots", lambda deposant: [(date(2024, 12, 31), "/faux.json")]
    )
    records, _ = client.annual_records("MC.PA")
    par_annee = {r.fiscal_year: r for r in records}
    assert par_annee[2024].get("revenue") == pytest.approx(84_683_000_000.0)


def test_ticker_inconnu_reste_silencieux(client):
    """Un titre absent de la table de correspondance ne doit produire ni
    erreur ni avertissement : la source est un complement."""
    records, avertissements = client.annual_records("AAPL")
    assert records == [] and avertissements == []


# ------------------------------------------- fusion Yahoo + ESEF
def _service(monkeypatch, recs_esef):
    """Service de fondamentaux dont seule la partie ESEF est simulee."""
    from investassist.config import load_settings
    from investassist.fundamentals import FundamentalsService

    svc = FundamentalsService.__new__(FundamentalsService)
    svc.settings = load_settings()

    class _Esef:
        def annual_records(self, ticker, *, avant_exercice=None):
            return list(recs_esef), []

    svc.esef = _Esef()
    return svc


def _annuel(annee, revenue, **extra):
    from investassist.models import AnnualRecord

    valeurs = {"revenue": revenue}
    valeurs.update(extra)
    return AnnualRecord(fiscal_year=annee, values=valeurs)


def test_fusion_allonge_l_historique_europeen(monkeypatch):
    """Yahoo s'arrete a quatre exercices ; le depot officiel en apporte un
    cinquieme, ce qui rend le TCAM europeen comparable au TCAM americain."""
    svc = _service(monkeypatch, [_annuel(2021, 64_220_000_000.0),
                                 _annuel(2022, 79_184_000_000.0)])
    by_year = {a: _annuel(a, 79_184_000_000.0 * (1.05 ** (a - 2022)))
               for a in (2022, 2023, 2024, 2025)}
    sources: dict[str, str] = {}

    message = svc._completer_par_esef("MC.PA", by_year, 5, sources)
    assert sorted(by_year) == [2021, 2022, 2023, 2024, 2025]
    assert "2021" in message
    assert sources["historique_ancien"].startswith("esef")


def test_fusion_remplit_un_exercice_vide_renvoye_par_yahoo(monkeypatch):
    """Yahoo renvoie parfois une colonne entierement vide pour l'exercice le
    plus ancien. L'exercice existe alors deja mais ne sert a rien : le
    completer est un gain, et doit etre annonce comme tel."""
    svc = _service(monkeypatch, [_annuel(2021, 64_220_000_000.0)])
    by_year = {2021: _annuel(2021, None), 2022: _annuel(2022, 79_184_000_000.0)}

    message = svc._completer_par_esef("MC.PA", by_year, 5, {})
    assert by_year[2021].get("revenue") == pytest.approx(64_220_000_000.0)
    assert "2021" in message


def test_fusion_refusee_si_les_sources_divergent(monkeypatch):
    """Deux perimetres de consolidation differents produiraient un taux de
    croissance faux : mieux vaut un historique court qu'un historique faux."""
    svc = _service(monkeypatch, [_annuel(2021, 10_000_000_000.0),
                                 _annuel(2022, 40_000_000_000.0)])  # Yahoo : 79 Md
    by_year = {a: _annuel(a, 79_184_000_000.0) for a in (2022, 2023, 2024, 2025)}

    message = svc._completer_par_esef("MC.PA", by_year, 5, {})
    assert 2021 not in by_year          # rien n'a ete ajoute
    assert "écarté" in message and "diverge" in message


def test_fusion_inutile_si_la_fenetre_est_deja_atteinte(monkeypatch):
    """Aucun telechargement de 5 Mo si l'historique suffit deja."""
    appels = []

    class _EsefCompteur:
        def annual_records(self, ticker, *, avant_exercice=None):
            appels.append(ticker)
            return [], []

    from investassist.config import load_settings
    from investassist.fundamentals import FundamentalsService

    svc = FundamentalsService.__new__(FundamentalsService)
    svc.settings = load_settings()
    svc.esef = _EsefCompteur()
    by_year = {a: _annuel(a, 1.0) for a in (2021, 2022, 2023, 2024, 2025)}

    assert svc._completer_par_esef("MC.PA", by_year, 5, {}) == ""
    assert appels == []
