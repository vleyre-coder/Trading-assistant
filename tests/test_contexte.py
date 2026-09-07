"""Tests du contexte macroeconomique et de l'actualite, sans appel reseau."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from investassist.config import load_settings
from investassist.providers.actualite import ActualiteClient, Article
from investassist.providers.macro import CODES_ZONE_EURO, Indicateur, MacroClient


class _CacheMuet:
    def get(self, *_):
        return None

    def set(self, *_):
        return None


# ============================================================ indicateurs
def test_indicateur_calcule_sa_variation():
    ind = Indicateur(
        cle="x", label="Taux", valeur=2.40, precedent=2.15, unite="percent"
    )
    assert ind.variation == pytest.approx(0.25)


def test_indicateur_sans_precedent_n_invente_pas_de_variation():
    ind = Indicateur(cle="x", label="Taux", valeur=2.40, unite="percent")
    assert ind.variation is None


def test_indicateur_perime_selon_sa_frequence():
    """Un indicateur publie avec retard n'est pas une erreur, mais le lire
    comme s'il etait courant en est une. Cas reel : l'inflation de la zone
    euro affichait decembre 2025 alors qu'on etait en septembre 2026."""
    vieux = Indicateur(
        cle="inflation", label="Inflation", valeur=2.0, unite="percent",
        publie_le=(date.today() - timedelta(days=200)).isoformat(),
        fraicheur_jours=60,
    )
    recent = Indicateur(
        cle="inflation", label="Inflation", valeur=2.0, unite="percent",
        publie_le=(date.today() - timedelta(days=10)).isoformat(),
        fraicheur_jours=60,
    )
    assert vieux.perime is True
    assert recent.perime is False


def test_indicateur_sans_date_de_publication_ne_se_declare_pas_perime():
    """Faute de date, on ne peut rien conclure : mieux vaut ne rien
    affirmer que d'afficher un avertissement infonde."""
    ind = Indicateur(cle="x", label="Taux", valeur=1.0, unite="percent")
    assert ind.perime is False


def test_chaine_de_codes_de_la_zone_euro_couvre_les_elargissements():
    """La composition de la zone euro change et son code change avec elle :
    coder « EA20 » en dur programme une panne silencieuse a la prochaine
    adhesion. Les series observees utilisent EA, EA20 et EA21."""
    assert "EA21" in CODES_ZONE_EURO
    assert "EA20" in CODES_ZONE_EURO
    assert "EA" in CODES_ZONE_EURO


def test_serie_eurostat_essaie_le_code_suivant_si_le_premier_est_vide(monkeypatch):
    """Un code geographique non reconnu renvoie une reponse VALIDE mais sans
    valeur : il faut passer au suivant, pas conclure a l'absence de donnee."""
    client = MacroClient.__new__(MacroClient)
    client.cache = _CacheMuet()
    client.session = None
    client.limiter = None

    appels: list[str] = []

    def faux_get_json(session, url, *, limiter=None, params=None, timeout=30.0, **_):
        appels.append(params["geo"])
        if params["geo"] != "EA20":
            return {"value": {}, "dimension": {"time": {"category": {"index": {}}}}}
        return {
            "value": {"0": 1.9, "1": 2.0},
            "updated": "2026-09-04T23:00:00+0200",
            "dimension": {"time": {"category": {"index": {"2026-06": 0, "2026-07": 1}}}},
        }

    monkeypatch.setattr("investassist.providers.macro.get_json", faux_get_json)
    obs, geo, publie = client._serie_eurostat("un_jeu", geos=("EA99", "EA20"))

    assert appels == ["EA99", "EA20"]      # le premier code a bien ete tente
    assert geo == "EA20"
    assert obs[-1] == ("2026-07", 2.0)
    assert publie.startswith("2026-09-04")


def test_indicateurs_omet_les_sources_muettes(monkeypatch):
    """Une source indisponible ne doit pas vider tout le tableau de bord."""
    client = MacroClient.__new__(MacroClient)
    client.cache = _CacheMuet()
    client.session = None
    client.limiter = None

    monkeypatch.setattr(
        MacroClient, "taux_directeur",
        lambda self: Indicateur(cle="ok", label="Taux", valeur=2.4, unite="percent"),
    )
    monkeypatch.setattr(
        MacroClient, "inflation_zone_euro",
        lambda self: Indicateur(cle="vide", label="Inflation", valeur=None, unite="percent"),
    )
    def _explose(self):
        raise RuntimeError("service en panne")
    for nom in ("croissance_zone_euro", "chomage_zone_euro",
                "taux_10_ans_zone_euro", "taux_americains"):
        monkeypatch.setattr(MacroClient, nom, _explose)

    obtenus = client.indicateurs()
    assert [i.cle for i in obtenus] == ["ok"]


# ============================================================== actualite
FLUX_MINIMAL = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Test</title>
  <item>
    <title>Communique de politique monetaire</title>
    <link>https://exemple.test/a</link>
    <pubDate>Wed, 29 Jul 2026 18:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Deuxieme communique</title>
    <link>https://exemple.test/b</link>
    <pubDate>Tue, 19 Aug 2026 14:30:00 GMT</pubDate>
  </item>
</channel></rss>"""

# Reproduit le flux de la Reserve federale : marqueur d'ordre d'octets en
# tete, et accents encodes en UTF-8 sans que l'en-tete HTTP le declare.
FLUX_AVEC_BOM = (
    b"\xef\xbb\xbf" + FLUX_MINIMAL.replace(
        b"Communique de politique monetaire",
        "Communiqué de politique monétaire".encode("utf-8"),
    )
)


def _client():
    objet = ActualiteClient.__new__(ActualiteClient)
    objet.cache = _CacheMuet()
    objet.session = None
    objet.limiter = None
    return objet


def test_extraction_d_un_flux_rss():
    articles = ActualiteClient._extraire(FLUX_MINIMAL, "url", 10)
    assert [a.titre for a in articles] == [
        "Communique de politique monetaire", "Deuxieme communique",
    ]
    assert articles[0].publie_le.startswith("2026-07-29")


def test_flux_avec_marqueur_d_ordre_d_octets_reste_lisible():
    """Le flux de la Reserve federale commence par un marqueur d'ordre
    d'octets. Analyse depuis une chaine deja decodee en ISO-8859-1, il
    echouait et le flux paraissait VIDE, sans aucune erreur visible. En
    lisant les octets, l'analyseur applique la declaration d'encodage du
    document et les accents survivent."""
    articles = ActualiteClient._extraire(FLUX_AVEC_BOM, "url", 10)
    assert len(articles) == 2
    assert articles[0].titre == "Communiqué de politique monétaire"


def test_flux_illisible_renvoie_une_liste_vide():
    assert ActualiteClient._extraire(b"ceci n'est pas du XML", "url", 5) == []


def test_limite_respectee():
    assert len(ActualiteClient._extraire(FLUX_MINIMAL, "url", 1)) == 1


def test_titre_trop_long_est_abrege():
    long = (
        b'<?xml version="1.0"?><rss><channel><item><title>'
        + b"a" * 500
        + b"</title><link>x</link></item></channel></rss>"
    )
    article = ActualiteClient._extraire(long, "url", 1)[0]
    assert len(article.titre) <= 220 and article.titre.endswith("…")


def test_ticker_invalide_n_emet_aucune_requete():
    """Un ticker n'est jamais interpole sans controle dans une URL."""
    client = _client()
    for suspect in ("../../etc/passwd", "", "a b", "AAPL&x=1", "<script>"):
        assert client.par_titre(suspect) == []


def test_ticker_avec_point_ou_tiret_est_accepte(monkeypatch):
    """Les places europeennes utilisent des suffixes : MC.PA, BRK-B."""
    client = _client()
    vus: list[str] = []
    monkeypatch.setattr(
        ActualiteClient, "_telecharger",
        lambda self, url: vus.append(url) or FLUX_MINIMAL,
    )
    assert len(client.par_titre("MC.PA")) == 2
    assert len(client.par_titre("BRK-B")) == 2
    assert "MC.PA" in vus[0] and "BRK-B" in vus[1]


def test_presse_et_institutionnel_restent_distincts(monkeypatch):
    """Separation essentielle : un titre de presse dit couramment « signal
    d'achat », formulation qui ne doit jamais pouvoir passer pour une sortie
    de cet outil."""
    client = _client()
    monkeypatch.setattr(
        ActualiteClient, "_telecharger", lambda self, url: FLUX_MINIMAL
    )
    assert all(a.nature == "institutionnel" for a in client.institutionnel(2))
    assert all(a.nature == "presse" for a in client.presse(2))


def test_date_rss_illisible_ne_fait_pas_echouer():
    from investassist.providers.actualite import _date_rss

    assert _date_rss("pas une date") == ""
    assert _date_rss("") == ""
    assert _date_rss("Wed, 29 Jul 2026 18:00:00 GMT").startswith("2026-07-29")


def test_article_en_dict_expose_sa_nature():
    a = Article(titre="T", lien="L", source="S", publie_le="2026-01-01T00:00:00+00:00")
    assert a.en_dict()["nature"] == "presse"
