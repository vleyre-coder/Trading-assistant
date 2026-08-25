"""Investassist — tableau de bord local d'analyse fondamentale.

Lancement :  streamlit run app.py

Usage strictement personnel. Cet outil ne fournit aucun conseil en
investissement : il classe des titres selon leur adequation a des criteres
fondamentaux objectifs, au moment du calcul.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from investassist import scoring  # noqa: E402
from investassist.alerts import Notifier, evaluate_rules  # noqa: E402
from investassist.alerts.rules import attach_earnings_dates  # noqa: E402
from investassist.config import (  # noqa: E402
    CONFIG_DIR,
    load_scoring,
    load_settings,
    load_universes,
)
from investassist.disclaimers import MAIN_HTML, ranking_phrasing  # noqa: E402
from investassist.fundamentals import FundamentalsService  # noqa: E402
from investassist.screener import ScreeningResult, Screener, tickers_for  # noqa: E402
from investassist.storage import ALERT_KINDS, Database, score_from_row  # noqa: E402
from investassist.ui import components as ui  # noqa: E402

st.set_page_config(page_title="Investassist — analyse fondamentale", page_icon="📊", layout="wide")

ALERT_LABELS = {
    "price_above": "Cours au-dessus d'un seuil",
    "price_below": "Cours au-dessous d'un seuil",
    "score_change": "Variation du score composite",
    "earnings_published": "Nouvelle publication de résultats",
    "top_n_entry": "Entrée dans le top N du classement",
    "top_n_exit": "Sortie du top N du classement",
}


# --------------------------------------------------------------- ressources
@st.cache_resource
def get_context():
    settings = load_settings()
    config = load_scoring()
    database = Database(settings.database_path)
    service = FundamentalsService(settings)
    screener = Screener(settings, config, service=service, database=database)
    return settings, config, database, service, screener


settings, config, db, service, screener = get_context()


@st.cache_data(ttl=3600, show_spinner=False)
def verifier_ticker(ticker: str) -> tuple[bool, str]:
    """Le ticker existe-t-il chez le fournisseur de donnees ?

    Evite qu'une faute de frappe (« AIRPA » au lieu de « AIR.PA ») cree
    silencieusement une watchlist ou une alerte qui ne se declenchera jamais.
    """
    snapshot = service.yahoo.snapshot(ticker)
    if snapshot is None or snapshot.price is None:
        return False, (
            f"Ticker « {ticker} » introuvable. Vérifiez le suffixe de place : "
            "AIR.PA (Paris), SAP.DE (Francfort), ASML.AS (Amsterdam), "
            "NESN.SW (Suisse), MSFT (États-Unis, sans suffixe)."
        )
    return True, snapshot.name or ticker


@st.cache_data(ttl=900, show_spinner=False)
def charger_titre(ticker: str, medianes: tuple[tuple[str, float], ...], fenetre: int):
    """Charge et note un titre, avec mise en cache de 15 minutes.

    Streamlit re-execute tout le script a chaque interaction : sans ce cache,
    ouvrir un menu deroulant relancait la lecture et l'analyse complete des
    etats financiers (plusieurs Mo de JSON EDGAR a reparser).
    """
    fundamentals = service.load(ticker, target_years=fenetre)
    prices = service.price_history(ticker, period="5y")
    score = scoring.score_stock(
        fundamentals, config, prices=prices, sector_medians=dict(medianes)
    )
    return fundamentals, prices, score


def run_screening(universes: list[str], use_cache: bool) -> None:
    progress = st.progress(0.0, text="Préparation de l'analyse…")

    def on_progress(done: int, total: int, ticker: str) -> None:
        progress.progress(done / max(total, 1), text=f"Analyse {done}/{total} — {ticker}")

    result = screener.run(universes, use_cache=use_cache, progress=on_progress)
    progress.empty()

    # Alertes : comparaison avec l'execution complete precedente.
    previous = db.previous_snapshot(result.run_id) if result.run_id else {}
    previous_ranks = {t: v["rank"] for t, v in previous.items() if v.get("rank")}
    attach_earnings_dates(result.scores, result.last_earnings)
    events = evaluate_rules(
        db,
        result.scores,
        config,
        previous=previous,
        ranks=result.ranks,
        previous_ranks=previous_ranks,
    )
    if events:
        Notifier(settings).dispatch(events)

    st.session_state["result"] = result
    st.session_state["result_at"] = datetime.now()
    st.session_state["last_events"] = events
    st.session_state["result_restaure"] = False


# ------------------------------------------------------------------ barre laterale
st.sidebar.title("📊 Investassist")
st.sidebar.caption("Analyse fondamentale — usage personnel")
view = st.sidebar.radio(
    "Vue",
    ["Classement", "Watchlist", "Alertes", "Historique des scores", "Méthodologie"],
    label_visibility="collapsed",
)
st.sidebar.divider()

catalogue = load_universes()
universe_names = list((catalogue.get("universes") or {}).keys())
default_selection = [u for u in (catalogue.get("default_selection") or []) if u in universe_names]
selected = st.sidebar.multiselect(
    "Univers analysés",
    universe_names,
    default=default_selection or universe_names[:1],
    format_func=lambda k: catalogue["universes"][k].get("label", k),
)
n_tickers = len(tickers_for(selected))
# Mesure observee : environ 3,5 s par titre sans cache, 4 requetes en
# parallele. Annoncer l'ordre de grandeur evite de croire l'outil bloque.
duree_estimee = max(1, round(n_tickers * 3.5 / 60))
st.sidebar.caption(
    f"{n_tickers} titres sélectionnés — environ {duree_estimee} min "
    "pour une analyse complète sans cache"
)

use_cache = st.sidebar.checkbox(
    "Utiliser le cache local",
    value=True,
    help=(
        f"Cache de {settings.cache_ttl_hours:.0f} h. Le décocher force la relecture "
        "complète des sources : plus lent et consommateur de quota gratuit."
    ),
)
if st.sidebar.button("🔄 Relancer l'analyse maintenant", type="primary", use_container_width=True):
    if not selected:
        st.sidebar.error("Sélectionnez au moins un univers.")
    else:
        run_screening(selected, use_cache)

if "exemple.fr" in settings.sec_user_agent:
    st.sidebar.warning(
        "Identification SEC non renseignée : les données officielles "
        "américaines (5 ans d'historique) risquent d'être refusées. "
        f"Renseignez `sec.user_agent` dans {CONFIG_DIR / 'settings.yaml'}."
    )

def restaurer_derniere_analyse() -> None:
    """Affiche le dernier classement enregistre des l'ouverture.

    Sans cela, l'utilisateur qui ouvre l'application le matin apres une
    execution planifiee la nuit voit un ecran vide et doit tout relancer.
    """
    if "result" in st.session_state:
        return
    dernier = db.last_run()
    if dernier is None:
        return
    lignes = db.scores_for_run(dernier["id"])
    if not lignes:
        return
    scores = [score_from_row(ligne) for ligne in lignes]
    st.session_state["result"] = ScreeningResult(
        scores=scores,
        ranked=scoring.rank(scores),
        excluded=scoring.excluded(scores),
        sector_medians=json.loads(dernier["sector_medians_json"] or "{}"),
        run_id=dernier["id"],
        universes=(dernier["universes"] or "").split(","),
    )
    st.session_state["result_at"] = datetime.fromisoformat(dernier["finished_at"])
    st.session_state["result_restaure"] = True


restaurer_derniere_analyse()

last_run = db.last_run()
if last_run:
    st.sidebar.caption(
        f"Dernière analyse enregistrée : {last_run['finished_at']} — "
        f"{last_run['n_ranked']}/{last_run['n_analyzed']} titres classés"
    )

result = st.session_state.get("result")


# ===================================================================== vues
def view_ranking() -> None:
    st.title("Classement par adéquation aux critères fondamentaux")
    ui.disclaimer_banner()

    if result is None:
        st.info(
            "Aucune analyse en mémoire pour cette session. Choisissez vos univers dans "
            "la barre latérale puis cliquez sur « Relancer l'analyse maintenant »."
        )
        return

    if st.session_state.get("result_restaure"):
        st.caption(
            "Classement restauré depuis la dernière analyse enregistrée localement. "
            "Les cours et les fondamentaux datent de cette exécution : relancez "
            "l'analyse pour les rafraîchir."
        )
    st.caption(
        f"Analyse du {st.session_state['result_at']:%d/%m/%Y à %H:%M} — "
        f"{len(result.ranked)} titres classés, {len(result.excluded)} exclus pour données "
        f"incomplètes"
        + (f", {len(result.failures)} échecs de récupération." if result.failures else ".")
    )

    if result.ranked:
        top = result.ranked[0]
        st.success(ranking_phrasing(top.ticker, 1) + f"  (score {top.composite:.1f}/100)")

    table = ui.ranking_table(result.ranked)
    st.dataframe(table, use_container_width=True, hide_index=True, height=520)
    st.download_button(
        "Exporter le classement (CSV)",
        table.to_csv(index=False).encode("utf-8"),
        file_name=f"classement_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )

    st.subheader("Pourquoi ce titre est à ce rang")
    tickers = [s.ticker for s in result.ranked]
    if tickers:
        chosen = st.selectbox("Titre", tickers, format_func=lambda t: f"{t} — rang {tickers.index(t) + 1}")
        score = next(s for s in result.ranked if s.ticker == chosen)
        st.markdown(f"**{ranking_phrasing(chosen, tickers.index(chosen) + 1)}**")
        ui.data_quality_notice(score)
        st.dataframe(ui.pillar_summary(score), use_container_width=True, hide_index=True)
        st.dataframe(ui.criteria_detail_table(score), use_container_width=True, hide_index=True)
        if st.button(f"➕ Ajouter {chosen} à ma watchlist"):
            db.add_to_watchlist(chosen)
            st.success(f"{chosen} ajouté à la watchlist.")

    if result.excluded:
        st.subheader("Données fondamentales incomplètes — titres exclus du classement")
        st.caption(
            "Ces titres ne sont pas classés : les afficher avec un score partiel "
            "produirait un classement trompeur."
        )
        st.dataframe(ui.excluded_table(result.excluded), use_container_width=True, hide_index=True)

    if result.failures:
        st.subheader("Échecs de récupération")
        st.dataframe(
            pd.DataFrame(
                [{"Ticker": k, "Erreur": v} for k, v in result.failures.items()]
            ),
            use_container_width=True,
            hide_index=True,
        )


def view_watchlist() -> None:
    st.title("Watchlist personnelle")
    ui.disclaimer_banner()

    with st.form("add_watch", clear_on_submit=True):
        columns = st.columns([2, 3, 1])
        ticker = columns[0].text_input("Ticker (convention Yahoo, ex. AIR.PA, MSFT)")
        note = columns[1].text_input("Note personnelle (optionnelle)")
        if columns[2].form_submit_button("Ajouter") and ticker.strip():
            symbole = ticker.strip().upper()
            with st.spinner(f"Vérification de {symbole}…"):
                valide, message = verifier_ticker(symbole)
            if valide:
                db.add_to_watchlist(symbole, note.strip())
                st.success(f"{symbole} — {message} — ajouté à la watchlist.")
            else:
                st.error(message)

    entries = db.watchlist()
    if not entries:
        st.info("Watchlist vide. Ajoutez un ticker ci-dessus.")
        return

    tickers = [e["ticker"] for e in entries]
    chosen = st.selectbox("Titre suivi", tickers)
    entry = next(e for e in entries if e["ticker"] == chosen)
    header = st.columns([4, 1])
    header[0].caption(f"Ajouté le {entry['added_at']}" + (f" — {entry['note']}" if entry["note"] else ""))
    if header[1].button("🗑️ Retirer"):
        db.remove_from_watchlist(chosen)
        st.rerun()

    # Les medianes sectorielles proviennent de la derniere analyse d'univers.
    # A defaut, le critere « P/E vs secteur » reste non calculable : comparer
    # un titre a lui-meme donnerait toujours un ratio de 1, donc un sous-score
    # artificiel de 60/100 sans aucune signification.
    medians = result.sector_medians if result else {}
    with st.spinner(f"Chargement des données de {chosen}…"):
        fundamentals, prices, score = charger_titre(
            chosen, tuple(sorted(medians.items())), config.target_years
        )

    if not medians:
        st.caption(
            "ℹ️ Critère « P/E vs médiane du secteur » non calculé : il nécessite "
            "un univers de comparaison. Lancez une analyse depuis la vue Classement "
            "pour l'activer."
        )

    st.subheader(f"{score.name or chosen} ({chosen})")
    ui.data_quality_notice(score)

    if prices is not None and not prices.empty:
        figure = go.Figure(
            go.Scatter(x=prices.index, y=prices["Close"], mode="lines", name="Cours")
        )
        figure.update_layout(
            title=f"Cours sur 5 ans — {chosen} ({score.currency or ''})",
            xaxis_title="", yaxis_title=f"Cours ({score.currency or ''})",
            height=340, margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(figure, use_container_width=True)
        st.caption(
            "Cours ajusté des divisions d'actions, non ajusté des dividendes. "
            "Le graphique est fourni à titre de contexte ; il n'entre pas dans le score."
        )
    else:
        st.warning("Historique de cours indisponible pour ce titre.")

    st.markdown("**Détail par critère**")
    st.dataframe(ui.criteria_detail_table(score), use_container_width=True, hide_index=True)

    st.markdown("**Historique des exercices utilisés**")
    rows = []
    for record in fundamentals.sorted_annual():
        rows.append(
            {
                "Exercice": record.fiscal_year,
                "Clôture": record.period_end,
                "Chiffre d'affaires": record.get("revenue"),
                "Résultat net": record.get("net_income"),
                "EBITDA": record.get("ebitda"),
                "Fonds propres": record.get("equity"),
                "Dette totale": record.get("total_debt"),
                "Trésorerie": record.get("cash"),
                "BPA dilué": record.get("eps_diluted"),
                "Dividende/action": record.get("dividend_per_share"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "Sources par champ : "
        + ", ".join(f"{k} → {v}" for k, v in sorted(fundamentals.sources.items()))
    )

    snapshot = fundamentals.snapshot
    if snapshot.next_earnings_date or snapshot.last_earnings_date:
        st.markdown("**Calendrier de publication**")
        st.write(
            f"Dernière période de référence connue : {snapshot.last_earnings_date or 'n/d'} — "
            f"prochaine publication annoncée : {snapshot.next_earnings_date or 'n/d'}"
        )


def view_alerts() -> None:
    st.title("Alertes sur seuils personnels")
    ui.disclaimer_banner()
    st.caption(
        "Une alerte signale le franchissement d'un seuil que vous définissez. "
        "Elle ne constitue ni une recommandation ni une incitation à agir."
    )

    channels = []
    if settings.alerts_local_enabled:
        channels.append("notification locale")
    if settings.alerts_email_enabled:
        channels.append("email SMTP")
    st.info(
        "Canaux actifs : " + (", ".join(channels) if channels else "aucun")
        + f" — configuration dans {CONFIG_DIR / 'settings.yaml'}"
    )

    with st.form("add_rule", clear_on_submit=True):
        columns = st.columns([2, 3, 2, 1])
        ticker = columns[0].text_input("Ticker")
        kind = columns[1].selectbox("Type d'alerte", ALERT_KINDS, format_func=lambda k: ALERT_LABELS[k])
        if kind in ("price_above", "price_below"):
            value = columns[2].number_input("Seuil de cours", min_value=0.0, step=1.0)
            params = {"threshold": value}
        elif kind == "score_change":
            value = columns[2].number_input(
                "Variation minimale (points de score)",
                min_value=0.5,
                value=float(config.score_change_threshold),
                step=0.5,
            )
            params = {"threshold": value}
        elif kind in ("top_n_entry", "top_n_exit"):
            value = columns[2].number_input(
                "N du top", min_value=1, value=int(config.top_n), step=1
            )
            params = {"n": int(value)}
        else:
            columns[2].caption("Aucun paramètre requis")
            params = {}
        if columns[3].form_submit_button("Créer") and ticker.strip():
            symbole = ticker.strip().upper()
            seuil = params.get("threshold")
            if kind in ("price_above", "price_below") and not seuil:
                # Un seuil nul rendrait la regle inopérante (ou toujours vraie).
                st.error("Renseignez un seuil de cours strictement positif.")
            else:
                with st.spinner(f"Vérification de {symbole}…"):
                    valide, message = verifier_ticker(symbole)
                if valide:
                    db.add_alert_rule(symbole, kind, params)
                    st.success(f"Règle créée pour {symbole} — {message}.")
                else:
                    st.error(message)

    rules = db.alert_rules(enabled_only=False)
    if rules:
        st.subheader("Règles configurées")
        for rule in rules:
            columns = st.columns([2, 4, 3, 1, 1])
            columns[0].write(f"**{rule['ticker']}**")
            columns[1].write(ALERT_LABELS.get(rule["kind"], rule["kind"]))
            columns[2].write(rule["params"] or "—")
            active = columns[3].toggle("Active", value=bool(rule["enabled"]), key=f"tg{rule['id']}")
            if active != bool(rule["enabled"]):
                db.set_rule_enabled(rule["id"], active)
                st.rerun()
            if columns[4].button("🗑️", key=f"del{rule['id']}"):
                db.delete_alert_rule(rule["id"])
                st.rerun()
    else:
        st.info("Aucune règle configurée.")

    st.subheader("Journal des alertes déclenchées")
    events = db.events(limit=100)
    if events:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Date": e["triggered_at"],
                        "Ticker": e["ticker"],
                        "Type": ALERT_LABELS.get(e["kind"], e["kind"]),
                        "Message": e["message"],
                        "Canal": e["channel"] or "—",
                    }
                    for e in events
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Aucune alerte déclenchée à ce jour.")


def view_history() -> None:
    st.title("Historique des scores")
    ui.disclaimer_banner()
    st.caption(
        "Évolution du score d'adéquation aux critères et du rang dans le classement, "
        "au fil des analyses enregistrées localement."
    )

    with db.connect() as conn:
        tickers = [
            r["ticker"]
            for r in conn.execute("SELECT DISTINCT ticker FROM scores ORDER BY ticker").fetchall()
        ]
    if not tickers:
        st.info("Aucun historique : lancez une première analyse depuis la vue Classement.")
        return

    chosen = st.selectbox("Titre", tickers)
    history = db.score_history(chosen)
    if not history:
        st.info("Pas d'historique pour ce titre.")
        return

    frame = pd.DataFrame(history)
    frame["computed_at"] = pd.to_datetime(frame["computed_at"])

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(x=frame["computed_at"], y=frame["composite"], mode="lines+markers", name="Score composite")
    )
    figure.update_layout(
        title=f"Score composite — {chosen}", height=320,
        yaxis_title="Score /100", margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(figure, use_container_width=True)

    if frame["rank"].notna().any():
        rank_figure = go.Figure(
            go.Scatter(x=frame["computed_at"], y=frame["rank"], mode="lines+markers", name="Rang")
        )
        rank_figure.update_layout(
            title=f"Rang dans le classement — {chosen}", height=280,
            yaxis_title="Rang", yaxis=dict(autorange="reversed"),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(rank_figure, use_container_width=True)

    criterion = st.selectbox(
        "Historique d'un critère",
        list(config.criteria.keys()),
        format_func=lambda k: config.criteria[k].label,
    )
    rows = db.criterion_history(chosen, criterion)
    if rows:
        detail = pd.DataFrame(rows)
        detail["computed_at"] = pd.to_datetime(detail["computed_at"])
        st.dataframe(detail, use_container_width=True, hide_index=True)
    else:
        st.caption("Aucun historique pour ce critère.")


def view_methodology() -> None:
    st.title("Méthodologie et limites")
    ui.disclaimer_banner(expanded_details=True)

    st.subheader("Pondération des piliers")
    st.dataframe(
        pd.DataFrame(
            [
                {"Pilier": ui.PILLAR_LABELS.get(k, k), "Poids": f"{v * 100:.0f} %"}
                for k, v in config.pillar_weights.items()
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"Modifiable dans {CONFIG_DIR / 'scoring.yaml'} — aucun poids n'est codé en dur.")

    st.subheader("Critères et barèmes")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Critère": c.label,
                    "Pilier": ui.PILLAR_LABELS.get(c.pillar, c.pillar),
                    "Poids dans le pilier": f"{c.weight * 100:.0f} %",
                    "Sens": "plus haut = mieux" if c.higher_is_better else "plus bas = mieux",
                    "Barème (valeur → score)": " ; ".join(f"{x:g}→{y:g}" for x, y in c.points),
                }
                for c in config.criteria.values()
                if c.enabled
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Règles d'exclusion")
    st.markdown(
        f"""
- Fenêtre visée : **{config.target_years} ans** ; minimum requis : **{config.min_years} ans**.
- Couverture minimale des critères pour être classé : **{config.min_weight_coverage * 100:.0f} %**.
- Un pilier dont la couverture est inférieure à **{config.min_pillar_coverage * 100:.0f} %** est
  neutralisé et son poids redistribué.
- Un titre sans dividende reçoit un score neutre de **{config.no_dividend_score:.0f}/100** sur ce
  pilier : les valeurs de croissance ne sont pas pénalisées.
- Un critère non calculable est marqué **n/d**, jamais remplacé par une valeur favorable
  (un PEG sans croissance positive n'est pas un « bon » PEG).
"""
    )

    st.subheader("Limites connues")
    st.markdown(
        f"""
- **{MAIN_HTML}**
- La couverture fondamentale gratuite est plus pauvre en Europe : les sociétés non cotées
  aux États-Unis n'ont pas de dépôt SEC, et Yahoo Finance ne fournit souvent que 3 à 4
  exercices. La fenêtre réellement utilisée est affichée pour chaque titre.
- Les critères de bilan (dette nette/EBITDA, ratio de liquidité) ne sont pas pertinents pour
  les banques et assureurs : le pilier correspondant est généralement neutralisé pour ces
  titres, ce qui est signalé dans le détail.
- yfinance est une bibliothèque non officielle : elle peut cesser de fonctionner sans préavis.
- Les données ne sont pas auditées et peuvent comporter des erreurs de source.
"""
    )


VIEWS = {
    "Classement": view_ranking,
    "Watchlist": view_watchlist,
    "Alertes": view_alerts,
    "Historique des scores": view_history,
    "Méthodologie": view_methodology,
}
VIEWS[view]()

st.divider()
st.caption(f"⚠️ {MAIN_HTML} — Investassist, outil personnel, aucune diffusion à des tiers.")
