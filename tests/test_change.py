"""Tests du change et des ratios qui melangent marche et comptabilite.

Le defaut corrige ici etait invisible : Yahoo expose la devise de COTATION
et celle des ETATS FINANCIERS, l'outil ne lisait que la premiere. Six titres
sur 143 publient dans une autre devise que celle ou ils cotent — dont PDD
Holdings, alors classe premier, dont le rendement du free cash flow
ressortait a 90 % au lieu de 13 %.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from investassist import criteria as crit
from investassist.models import AnnualRecord, Fundamentals, Snapshot
from investassist.providers.change import ChangeClient


# ------------------------------------------------------------ echafaudage
CSV_CNY = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
EXR.D.CNY.EUR.SP00.A,D,CNY,EUR,SP00,A,2021-12-30,7.2230
EXR.D.CNY.EUR.SP00.A,D,CNY,EUR,SP00,A,2021-12-31,7.1947
EXR.D.CNY.EUR.SP00.A,D,CNY,EUR,SP00,A,2026-09-08,7.7936
"""
CSV_USD = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2021-12-30,1.1326
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2021-12-31,1.1326
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-08,1.1614
"""


class _CacheMuet:
    def get(self, *_):
        return None

    def set(self, *_):
        return None


def _client(reponses: dict[str, str] | None = None) -> ChangeClient:
    """Client de change dont les series sont fournies, sans reseau."""
    objet = ChangeClient.__new__(ChangeClient)
    objet.cache = _CacheMuet()
    objet.session = None
    objet.limiter = None
    objet._series = {}
    if reponses is not None:
        import csv as _csv
        import io as _io
        for devise, brut in reponses.items():
            points = []
            for ligne in _csv.DictReader(_io.StringIO(brut)):
                points.append((ligne["TIME_PERIOD"], float(ligne["OBS_VALUE"])))
            objet._series[devise] = sorted(points)
    return objet


class _ChangeFixe:
    """Taux constant, pour isoler l'effet de la conversion dans un critere."""

    def __init__(self, valeur: float | None) -> None:
        self.valeur = valeur
        self.appels: list[tuple[str, str, date | None]] = []

    def facteur(self, de: str, vers: str, le: date | None = None) -> float | None:
        self.appels.append((de, vers, le))
        return 1.0 if de == vers else self.valeur


def _fund(**kw) -> Fundamentals:
    instantane = Snapshot(
        ticker="TST",
        currency=kw.pop("cotation", "USD"),
        reporting_currency=kw.pop("etats", None),
        market_cap=kw.pop("cap", None),
        trailing_pe=kw.pop("pe", None),
    )
    annees = kw.pop("annees", [])
    return Fundamentals(
        ticker="TST", snapshot=instantane,
        annual=[AnnualRecord(fiscal_year=a, values=v) for a, v in annees],
        change=kw.pop("change", None),
    )


# ================================================================= taux
def test_l_euro_ne_demande_aucun_taux():
    """La moitie de l'univers cote en euro : lui faire traverser le reseau
    pour apprendre qu'un euro vaut un euro serait un appel gaspille."""
    client = _client({})
    assert client.facteur("EUR", "EUR") == 1.0
    assert client.serie("EUR") == [("1999-01-01", 1.0)]


def test_croisement_passe_par_l_euro():
    """La BCE ne publie que des taux « pour un euro » : un CNY/USD se
    construit donc par division, il n'existe pas en direct."""
    client = _client({"CNY": CSV_CNY, "USD": CSV_USD})
    facteur = client.facteur("CNY", "USD")
    assert facteur == pytest.approx(1.1614 / 7.7936)
    # Sens inverse : produit des deux facteurs egal a 1.
    assert facteur * client.facteur("USD", "CNY") == pytest.approx(1.0)


def test_taux_pris_a_la_date_demandee_ou_avant():
    """Un exercice clos un 31 decembre tombe souvent un jour sans cotation.
    Prendre « la date la plus proche » irait chercher un taux POSTERIEUR a
    la cloture : on ne lit jamais l'avenir, on prend le dernier taux connu."""
    client = _client({"CNY": CSV_CNY, "USD": CSV_USD})
    assert client._pour_un_euro("CNY", date(2021, 12, 31)) == 7.1947
    assert client._pour_un_euro("CNY", date(2022, 1, 3)) == 7.1947   # dernier connu
    assert client._pour_un_euro("CNY", date(2021, 12, 30)) == 7.2230


def test_date_anterieure_a_la_serie_ne_renvoie_pas_le_premier_taux():
    """Appliquer le taux de 2021 a un exercice 2015 serait une invention."""
    client = _client({"CNY": CSV_CNY})
    assert client._pour_un_euro("CNY", date(2015, 6, 30)) is None


def test_devise_inconnue_renvoie_none_et_non_un():
    """Un facteur 1,0 par defaut produirait un ratio faux presente comme
    exact : c'est precisement le defaut que ce module corrige."""
    # Serie vide : c'est ce que renvoie la BCE pour un code de devise
    # qu'elle ne publie pas.
    client = _client({"XYZ": "", "USD": CSV_USD})
    assert client.facteur("XYZ", "USD") is None
    assert client.convertir(100.0, "XYZ", "USD") is None


# ========================================================== fundamentals
def test_devises_identiques_ne_demandent_aucune_source_de_taux():
    f = _fund(cotation="USD", etats="USD")
    assert f.devises_divergentes is False
    assert f.facteur_vers_etats() == 1.0


def test_devise_des_etats_absente_retombe_sur_la_cotation():
    """Cas de 137 titres sur 143 : Yahoo ne renseigne pas toujours le champ,
    et l'immense majorite publie dans sa devise de cotation."""
    f = _fund(cotation="EUR", etats=None)
    assert f.devise_etats == "EUR"
    assert f.devises_divergentes is False


def test_divergence_sans_source_de_taux_ne_devine_pas():
    f = _fund(cotation="USD", etats="CNY", change=None)
    assert f.devises_divergentes is True
    assert f.facteur_vers_etats() is None
    assert f.vers_etats(100.0) is None


# =============================================================== criteres
def test_rendement_du_free_cash_flow_converti():
    """Cas reel de PDD Holdings : 105,8 Md CNY de free cash flow et 117,1 Md
    USD de capitalisation. Non converti, le rendement ressortait a 90 % et
    saturait le bareme ; converti, il vaut environ 13 %."""
    annees = [(2025, {"free_cash_flow": 105.79e9, "revenue": 450.8e9})]
    brut = _fund(cotation="USD", etats="USD", cap=117.07e9, annees=annees)
    valeur_fausse, _, _ = crit.fcf_yield(brut)
    assert valeur_fausse == pytest.approx(0.904, abs=0.01)

    # 1 CNY = 0,149 USD, donc 1 USD = 6,71 CNY : la capitalisation vaut
    # 785 Md CNY, et le rendement 13,5 %.
    juste = _fund(cotation="USD", etats="CNY", cap=117.07e9, annees=annees,
                  change=_ChangeFixe(1 / 0.1490))
    valeur, detail, _ = crit.fcf_yield(juste)
    assert valeur == pytest.approx(0.135, abs=0.005)
    assert "convertie de USD" in detail
    assert "CNY" in detail


def test_rendement_du_free_cash_flow_sans_taux_se_declare_indisponible():
    annees = [(2025, {"free_cash_flow": 105.79e9})]
    f = _fund(cotation="USD", etats="CNY", cap=117.07e9, annees=annees, change=_ChangeFixe(None))
    valeur, _, motif = crit.fcf_yield(f)
    assert valeur is None
    assert "taux de change USD/CNY indisponible" in motif


def test_valeur_d_entreprise_sur_ventes_convertie():
    annees = [(2025, {"revenue": 450.8e9, "total_debt": 38.5e9, "cash": 405.2e9})]
    f = _fund(cotation="USD", etats="CNY", cap=117.07e9, annees=annees,
              change=_ChangeFixe(1 / 0.1490))
    valeur, detail, _ = crit.ev_to_sales(f)
    # (785,7 - 366,7) / 450,8 ~ 0,93
    assert valeur == pytest.approx(0.93, abs=0.03)
    assert "convertie de USD" in detail


def test_pe_historique_utilise_le_taux_de_la_cloture_et_non_celui_du_jour():
    """Un P/E passe se lit avec le change de l'epoque. Utiliser le taux
    courant melangerait un cours de 2021 a une parite de 2026."""
    cours = pd.DataFrame(
        {"Close": [100.0, 200.0]},
        index=pd.to_datetime(["2021-12-31", "2025-12-31"]),
    )
    annees = [(2021, {"eps_diluted": 1.0, "revenue": 1.0}),
              (2025, {"eps_diluted": 2.0, "revenue": 2.0})]
    f = _fund(cotation="USD", etats="CNY", annees=annees, change=_ChangeFixe(7.0))
    for rec in f.annual:
        rec.period_end = date(rec.fiscal_year, 12, 31)
    histoire, motif = crit.historical_pe(f, cours)
    assert motif == ""
    assert histoire == [(2021, pytest.approx(700.0)), (2025, pytest.approx(700.0))]
    # Le taux a bien ete demande a la date de cloture de chaque exercice.
    assert {appel[2] for appel in f.change.appels} == {date(2021, 12, 31), date(2025, 12, 31)}


def test_pe_historique_sans_taux_le_dit_au_lieu_de_paraitre_incomplet():
    """Sans ce message, l'utilisateur lisait « aucun exercice avec BPA
    positif », un diagnostic faux."""
    cours = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2025-12-31"]))
    annees = [(2025, {"eps_diluted": 2.0, "revenue": 2.0})]
    f = _fund(cotation="USD", etats="CNY", annees=annees, change=_ChangeFixe(None))
    for rec in f.annual:
        rec.period_end = date(2025, 12, 31)
    histoire, motif = crit.historical_pe(f, cours)
    assert histoire == []
    assert "taux de change USD/CNY indisponible" in motif


def test_capitalisation_ramenee_a_l_euro():
    """Comparer 100 milliards de dollars a 100 milliards d'euros comme s'ils
    etaient egaux avantageait les titres americains d'environ 15 %."""
    f = _fund(cotation="USD", etats="USD", cap=100e9, change=_ChangeFixe(0.861))
    valeur, detail, _ = crit.market_size(f)
    assert valeur == pytest.approx(86.1)
    assert "EUR" in detail and "converti de 100" in detail


def test_capitalisation_sans_taux_garde_la_valeur_brute_en_le_disant():
    """Perdre le critere serait pire que l'approximation — a condition de
    l'annoncer plutot que de la taire."""
    f = _fund(cotation="USD", etats="USD", cap=100e9, change=_ChangeFixe(None))
    valeur, detail, motif = crit.market_size(f)
    assert valeur == pytest.approx(100.0)
    assert motif == ""
    assert "non convertie" in detail
