"""Une seule monnaie par exercice — tests de non-regression.

Le defaut mesure sur PDD Holdings : les comptes deposes a la SEC existent en
yuan ET en dollar, mais pas pour toutes les balises ni toutes les annees. La
lecture choisissait la devise balise par balise, et la source
complementaire versait ses valeurs sans se poser la question. Resultat, dans
un meme exercice : chiffre d'affaires 61,8 Md USD et marge brute 243,0 Md CNY
— soit une marge brute de 394 % du chiffre d'affaires et un free cash flow
superieur aux ventes. Aucun message d'erreur, un titre premier du classement.
"""
from __future__ import annotations

from datetime import date

import pytest

from investassist.models import AnnualRecord, Fundamentals, Snapshot
from investassist.providers.edgar import EdgarClient
from investassist.providers.esef import EsefClient, devise_de_l_unite


# =============================================================== EDGAR
def _faits(series: dict[str, dict[str, list[int]]]) -> dict:
    """Faits SEC minimaux : {balise: {devise: [exercices]}}."""
    tags = {}
    for tag, par_devise in series.items():
        unites = {}
        for devise, annees in par_devise.items():
            unites[devise] = [
                {"form": "20-F", "start": f"{a}-01-01", "end": f"{a}-12-31",
                 "filed": f"{a + 1}-03-01", "val": 100.0 + a}
                for a in annees
            ]
        tags[tag] = {"units": unites}
    return {"facts": {"us-gaap": tags}}


def _client() -> EdgarClient:
    objet = EdgarClient.__new__(EdgarClient)
    objet._devise = None
    objet._period_ends = {}
    objet._filed = {}
    return objet


def test_devise_retenue_est_celle_qui_couvre_la_fenetre_recente():
    """Le yuan couvre plus d'annees au total, le dollar couvre la fenetre
    analysee : c'est la fenetre qui compte, pas la longueur de l'historique."""
    faits = _faits({
        "Revenues": {"CNY": list(range(2016, 2026)), "USD": list(range(2018, 2026))},
        "NetIncomeLoss": {"CNY": list(range(2016, 2026)), "USD": list(range(2018, 2026))},
    })
    assert _client()._devise_des_comptes(faits, fenetre=5) == "USD"


def test_devise_retenue_suit_la_meilleure_couverture_recente():
    """Un emetteur qui ne traduit en dollar que son dernier exercice doit
    etre lu dans SA devise, pas en dollar avec quatre trous."""
    faits = _faits({
        "Revenues": {"EUR": list(range(2021, 2026)), "USD": [2025]},
        "NetIncomeLoss": {"EUR": list(range(2021, 2026)), "USD": [2025]},
    })
    assert _client()._devise_des_comptes(faits, fenetre=5) == "EUR"


def test_aucun_fait_monetaire_retombe_sur_le_dollar():
    assert _client()._devise_des_comptes({"facts": {}}) == "USD"


def test_lecture_ne_retombe_jamais_sur_une_autre_devise():
    """Le coeur du defaut : la marge brute n'existait qu'en yuan, elle etait
    donc lue en yuan et posee a cote d'un chiffre d'affaires en dollar."""
    faits = _faits({
        "Revenues": {"USD": [2025], "CNY": [2025]},
        "GrossProfit": {"CNY": [2025]},         # pas de serie en dollar
    })
    client = _client()
    client._devise = "USD"
    assert client._units(faits, "Revenues") is not None
    assert client._units(faits, "GrossProfit") is None


def test_unites_non_monetaires_restent_lisibles():
    """Un nombre d'actions ne depend d'aucune devise : l'ecarter priverait
    le critere de dilution de sa donnee."""
    faits = {"facts": {"us-gaap": {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {
            "units": {"shares": [{"form": "20-F", "start": "2025-01-01",
                                  "end": "2025-12-31", "val": 1e9}]}
        }}}}
    client = _client()
    client._devise = "EUR"
    assert client._units(faits, "WeightedAverageNumberOfDilutedSharesOutstanding")


def test_donnees_par_action_suivent_la_devise_retenue():
    faits = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {
        "CNY/shares": [{"form": "20-F", "end": "2025-12-31", "val": 9.0}],
        "USD/shares": [{"form": "20-F", "end": "2025-12-31", "val": 1.3}],
    }}}}}
    client = _client()
    client._devise = "USD"
    entrees = client._units(faits, "EarningsPerShareDiluted")
    assert entrees and entrees[0]["val"] == 1.3


# ================================================================ ESEF
def test_devise_lue_dans_l_unite_xbrl():
    """Formes reelles observees sur filings.xbrl.org."""
    assert devise_de_l_unite("iso4217:EUR") == "EUR"
    assert devise_de_l_unite("iso4217:EUR/xbrli:shares") == "EUR"
    assert devise_de_l_unite("xbrli:shares") is None
    assert devise_de_l_unite("") is None


def test_devise_du_depot_est_la_dominante():
    faits = {"faits": [
        {"unit": "iso4217:EUR"}, {"unit": "iso4217:EUR"},
        {"unit": "iso4217:USD"}, {"unit": "xbrli:shares"},
    ]}
    assert EsefClient.devise_du_depot(faits) == "EUR"
    assert EsefClient.devise_du_depot({"faits": [{"unit": "xbrli:shares"}]}) is None


def test_fait_dans_une_autre_devise_est_ecarte():
    assert EsefClient._bonne_devise({"unit": "iso4217:EUR"}, "EUR") is True
    assert EsefClient._bonne_devise({"unit": "iso4217:USD"}, "EUR") is False
    # Non monetaire : conserve quelle que soit la devise retenue.
    assert EsefClient._bonne_devise({"unit": "xbrli:shares"}, "EUR") is True
    # Devise inconnue : on ne filtre rien plutot que de tout jeter.
    assert EsefClient._bonne_devise({"unit": "iso4217:USD"}, None) is True


# ======================================================== fundamentals
def test_devise_des_comptes_prime_sur_les_metadonnees_de_marche():
    """Yahoo annonce le yuan pour PDD Holdings alors que les comptes retenus
    viennent de la SEC et sont en dollar. Croire la metadonnee conduisait a
    convertir une capitalisation deja homogene, donc a fabriquer une erreur
    en croyant en corriger une."""
    f = Fundamentals(
        ticker="PDD",
        snapshot=Snapshot(ticker="PDD", currency="USD", reporting_currency="CNY"),
        annual=[AnnualRecord(fiscal_year=2025, values={"revenue": 1.0}, devise="USD")],
        devise_comptes="USD",
    )
    assert f.devise_etats == "USD"
    assert f.devises_divergentes is False
    assert f.facteur_vers_etats() == 1.0


def test_sans_devise_constatee_on_retombe_sur_la_metadonnee():
    f = Fundamentals(
        ticker="X",
        snapshot=Snapshot(ticker="X", currency="EUR", reporting_currency="USD"),
        annual=[],
    )
    assert f.devise_etats == "USD"


def test_facteur_de_completion_utilise_la_cloture_de_l_exercice_cible():
    """Un poste de 2022 se convertit avec la parite de 2022, pas celle du
    jour : sinon la serie annuelle porte le change d'aujourd'hui."""
    from investassist.fundamentals import FundamentalsService

    class _Change:
        def __init__(self):
            self.appels = []

        def facteur(self, de, vers, le=None):
            self.appels.append((de, vers, le))
            return 0.15

    service = FundamentalsService.__new__(FundamentalsService)
    service.change = _Change()

    source = AnnualRecord(fiscal_year=2022, period_end=date(2022, 12, 31),
                          values={"gross_profit": 100.0}, devise="CNY")
    cible = AnnualRecord(fiscal_year=2022, period_end=date(2022, 12, 31),
                         values={"revenue": 10.0}, devise="USD")
    assert service._facteur_de_completion(source, cible) == 0.15
    assert service.change.appels == [("CNY", "USD", date(2022, 12, 31))]


def test_facteur_de_completion_vaut_un_sans_appel_quand_les_devises_coincident():
    """Cas de la quasi-totalite des titres : aucun taux a chercher."""
    from investassist.fundamentals import FundamentalsService

    class _ChangeInterdit:
        def facteur(self, *_, **__):
            raise AssertionError("aucun taux ne doit etre demande")

    service = FundamentalsService.__new__(FundamentalsService)
    service.change = _ChangeInterdit()
    a = AnnualRecord(fiscal_year=2025, values={}, devise="USD")
    b = AnnualRecord(fiscal_year=2025, values={}, devise="USD")
    assert service._facteur_de_completion(a, b) == 1.0
    # Devise inconnue d'un cote : on ne convertit pas, faute de savoir quoi.
    assert service._facteur_de_completion(AnnualRecord(fiscal_year=2025, values={}), b) == 1.0


# ======================================== lecture concurrente (EDGAR)
def test_deux_titres_lus_en_parallele_gardent_leurs_dates_de_cloture():
    """L'etat de lecture vivait sur l'instance du client, partagee par les
    quatre fils d'execution du screener : un titre lu pendant qu'un autre
    demarrait se retrouvait sans AUCUNE date de cloture.

    Consequences silencieuses : le P/E historique disparait (il a besoin de
    la cloture pour retrouver le cours de fin d'exercice) et le retraitement
    des divisions d'actions perd sa date de reference — le defaut meme que
    ce projet documente comme faussant NVIDIA d'un facteur 4 a 40.

    Le scenario ci-dessous force l'entrelacement au lieu de l'esperer.
    """
    import threading

    def _serie(fin: int, valeur: float) -> dict:
        def duree(a):
            return {"form": "10-K", "start": f"{a}-01-01", "end": f"{a}-12-31",
                    "filed": f"{a + 1}-02-01", "val": valeur}

        def instant(a):
            return {"form": "10-K", "end": f"{a}-12-31",
                    "filed": f"{a + 1}-02-01", "val": valeur}

        annees = range(fin - 4, fin + 1)
        return {"facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [duree(a) for a in annees]}},
            "NetIncomeLoss": {"units": {"USD": [duree(a) for a in annees]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [duree(a) for a in annees]}},
            "StockholdersEquity": {"units": {"USD": [instant(a) for a in annees]}},
        }}}

    client = EdgarClient.__new__(EdgarClient)
    client._ticker_map = {"LENT": "1", "RAPIDE": "2"}
    cartes = {"1": _serie(2025, 10.0), "2": _serie(2020, 99.0)}
    client.company_facts = lambda cik: cartes[cik]

    pret, fini = threading.Event(), threading.Event()
    vrais_instants = client._instant_values

    def instants_espion(facts, tags, **kw):
        resultat = vrais_instants(facts, tags, **kw)
        if facts is cartes["1"] and not pret.is_set():
            pret.set()        # LENT a fini de lire ses flux
            fini.wait(5)      # ... et laisse RAPIDE demarrer sa propre lecture
        return resultat

    client._instant_values = instants_espion

    sorties: dict[str, list[AnnualRecord]] = {}

    def lire(ticker: str) -> None:
        sorties[ticker] = client.annual_records(ticker)[0]

    lent = threading.Thread(target=lire, args=("LENT",))
    rapide = threading.Thread(target=lire, args=("RAPIDE",))
    lent.start()
    assert pret.wait(5), "le fil lent n'a pas atteint le point d'entrelacement"
    rapide.start()
    rapide.join(10)
    fini.set()
    lent.join(10)

    for ticker, attendu in (("LENT", 2025), ("RAPIDE", 2020)):
        recs = sorties[ticker]
        assert [r.fiscal_year for r in recs] == list(range(attendu - 4, attendu + 1))
        for r in recs:
            assert r.period_end == date(r.fiscal_year, 12, 31), (
                f"{ticker} exercice {r.fiscal_year} : cloture {r.period_end}"
            )
            assert r.filed.get("eps_diluted") == f"{r.fiscal_year + 1}-02-01"


# ============================ totaux consolides (chiffre d'affaires)
def test_chiffre_d_affaires_retient_le_total_et_non_une_de_ses_lignes():
    """Certains emetteurs reservent « revenus des contrats clients » a UNE
    LIGNE de leur compte de resultat et publient le total sous « Revenus ».
    La chaine de priorite s'arretait a la premiere balise renseignee.

    Mesure sur l'univers analyse : Charter Communications ressortait a
    889 M$ de chiffre d'affaires au lieu de 54,8 Md$ — 98 % d'ecart, une
    marge brute de 3 111 % et une croissance entierement fausse. MercadoLibre
    a 20,3 Md$ au lieu de 28,9 Md$. Un total n'ayant aucune composante plus
    grande que lui, la plus grande valeur de l'exercice gagne.
    """
    faits = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerIncludingAssessedTax": {"units": {"USD": [
            {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
             "filed": "2026-02-01", "val": 889_000_000.0}]}},
        "Revenues": {"units": {"USD": [
            {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
             "filed": "2026-02-01", "val": 54_774_000_000.0}]}},
    }}}
    client = _client()
    serie = client._annual_flows(
        faits,
        ("RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues"),
        total_consolide=True,
    )
    assert serie[2025][1] == 54_774_000_000.0


def test_les_autres_agregats_gardent_la_priorite_des_balises():
    """« La plus grande » n'a aucun sens pour un resultat net : part du
    groupe et ensemble consolide sont deux definitions legitimes, et la plus
    grande n'est pas la bonne. Seul le chiffre d'affaires est traite en total.
    """
    faits = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
             "filed": "2026-02-01", "val": 100.0}]}},
        "ProfitLoss": {"units": {"USD": [
            {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
             "filed": "2026-02-01", "val": 140.0}]}},
    }}}
    serie = _client()._annual_flows(faits, ("NetIncomeLoss", "ProfitLoss"))
    assert serie[2025][1] == 100.0

    from investassist.providers.edgar import TOTAUX_CONSOLIDES
    assert TOTAUX_CONSOLIDES == {"revenue"}


def test_publication_la_plus_recente_gagne_toujours():
    """Un retraitement ecrase la premiere version publiee du meme exercice —
    y compris en mode total consolide, ou la comparaison porte ensuite sur
    les valeurs.
    """
    faits = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
         "filed": "2026-02-01", "val": 100.0},
        {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
         "filed": "2027-02-01", "val": 90.0},   # retraite a la baisse
    ]}}}}}
    for mode in (False, True):
        serie = _client()._annual_flows(faits, ("Revenues",), total_consolide=mode)
        assert serie[2025][1] == 90.0, f"mode total_consolide={mode}"


# ================================= fenetre d'analyse contigue
def test_la_fenetre_d_analyse_n_enjambe_jamais_un_trou():
    """« Les cinq derniers exercices disponibles » n'est pas « les cinq
    derniers exercices ». Quand une source couvre les annees anciennes et
    l'autre les recentes, la liste saute des annees.

    Cas mesure sur Xcel Energy : fenetre retenue 2018, 2022, 2023, 2024,
    2025. La croissance annoncee « sur 5 exercices » etait calculee de 2018 a
    2025 — sept annees — et l'evolution de la marge comparait la moyenne
    2018-2022 a la moyenne 2024-2025. Mieux vaut quatre exercices reels.
    """
    from investassist.fundamentals import fenetre_contigue

    exercices = [
        AnnualRecord(fiscal_year=a, period_end=date(a, 12, 31), values={"revenue": 1.0})
        for a in (2007, 2008, 2018, 2022, 2023, 2024, 2025)
    ]
    fenetre = fenetre_contigue(exercices, 5)
    assert [r.fiscal_year for r in fenetre] == [2022, 2023, 2024, 2025]
    portee = fenetre[-1].fiscal_year - fenetre[0].fiscal_year
    assert portee == len(fenetre) - 1, "la fenetre doit etre contigue"


def test_une_serie_contigue_reste_a_la_fenetre_visee():
    """Controle de non-regression : le cas normal ne doit pas etre rogne."""
    from investassist.fundamentals import fenetre_contigue

    exercices = [
        AnnualRecord(fiscal_year=a, period_end=date(a, 12, 31), values={"revenue": 1.0})
        for a in range(2016, 2026)
    ]
    fenetre = fenetre_contigue(exercices, 5)
    assert [r.fiscal_year for r in fenetre] == [2021, 2022, 2023, 2024, 2025]


def test_fenetre_sur_une_liste_vide_ou_d_un_seul_exercice():
    from investassist.fundamentals import fenetre_contigue

    assert fenetre_contigue([], 5) == []
    seul = [AnnualRecord(fiscal_year=2025, values={"revenue": 1.0})]
    assert [r.fiscal_year for r in fenetre_contigue(seul, 5)] == [2025]
