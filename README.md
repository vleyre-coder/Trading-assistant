# Investassist

Outil **personnel** d'aide à la décision d'investissement par l'analyse
fondamentale. Tableau de bord local (Python + Streamlit) qui classe un univers
d'actions européennes et américaines selon des critères fondamentaux objectifs,
avec le détail du calcul pour chaque titre.

> ### ⚠️ Ce que cet outil n'est pas
>
> **Le classement reflète l'adéquation aux critères fondamentaux au moment du
> calcul — pas une prédiction de mouvement de prix futur.** Un titre en tête de
> classement présente de bons fondamentaux de croissance, de qualité et de
> valorisation *selon les données disponibles aujourd'hui* ; cela n'indique ni
> le moment, ni la direction, ni la certitude d'une évolution de cours.
>
> Cet outil ne fournit **aucun conseil en investissement**, ne constitue pas une
> recommandation personnalisée et n'a pas vocation à être partagé à des tiers.
> Les données proviennent de sources gratuites non auditées, pouvant être
> incomplètes, retardées ou erronées.
>
> L'avertissement `Classement calculé à partir de données fondamentales
> historiques et actuelles. Ne constitue pas un conseil en investissement ni une
> prédiction de performance future.` est affiché sur **chaque** écran de
> résultat. Un test automatisé (`tests/test_disclaimers.py`) vérifie qu'aucune
> vue ne peut être ajoutée sans lui, et qu'aucune formulation prescriptive
> (« achetez X », « X va monter ») n'apparaît dans le code.

---

## Installation

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt

cp config/settings.example.yaml config/settings.yaml
# puis éditer config/settings.yaml (au minimum : sec.user_agent)
```

Python 3.11 ou plus. `config/settings.yaml` est dans `.gitignore` : les
identifiants SMTP et la clé API n'y seront jamais committés.

## Utilisation

```bash
# 1. Valider UN titre de bout en bout (à faire avant tout screening large)
python scripts/validate_ticker.py MSFT
python scripts/validate_ticker.py AIR.PA --no-cache

# 2. Lancer le tableau de bord
streamlit run app.py

# 3. Analyse complète en ligne de commande (pour une tâche planifiée)
python scripts/run_screening.py --universes cac40,nasdaq100
```

`validate_ticker.py` affiche chaque donnée brute récupérée, chaque critère,
son sous-score et le détail du calcul. C'est l'outil à utiliser en premier :
une anomalie de parsing s'y voit sur 3 appels réseau, au lieu d'être noyée
dans une exécution de 140 titres.

## Sources de données — état vérifié le 23 août 2026

Les plans gratuits évoluent souvent. Les limites ci-dessous ont été **testées
par appel réel**, pas reprises d'une documentation.

| Source | Clé | Vérifié | Ce qu'elle apporte réellement |
|---|---|---|---|
| **SEC EDGAR** (`data.sec.gov`, API XBRL `companyfacts`) | aucune | HTTP 200, illimité | **≥ 6 exercices annuels** exploitables : CA, résultat net, résultat opérationnel, amortissements, capitaux propres, dette, trésorerie, actifs/passifs courants, BPA dilué, dividende déclaré. Sociétés déposant aux **États-Unis** uniquement. Limite officielle : 10 requêtes/s, User-Agent identifiant obligatoire. |
| **Yahoo Finance** via `yfinance` | aucune | fonctionnel | Cours (5 ans), capitalisation, P/E, P/B, ROE, rendement, secteur, calendrier de publication, dividendes et divisions d'actions. Couvre l'Europe. **Mais seulement 4 à 5 exercices** dans les états financiers annuels. |
| **Financial Modeling Prep** | requise (gratuite) | 250 req/jour | Actions **US uniquement** (le global est payant), 5 ans de cours, **5 trimestres** d'états financiers, 500 Mo/30 jours. Les points d'entrée `/api/v3/` sont en fin de vie au profit de `/stable/`. |

### Pourquoi FMP n'est qu'une source d'appoint

Avec **5 trimestres** d'états financiers, le plan gratuit de FMP ne permet pas
de calculer une croissance sur 5 ans. EDGAR fait mieux, gratuitement, sans clé
et sans quota. `src/investassist/providers/fmp.py` est donc branché en
**contrôle croisé** : il compare nos ratios calculés à ceux de FMP et signale
un écart supérieur à 25 % (symptôme possible d'une erreur de parsing de notre
côté, ou d'une définition différente de l'agrégat). Pour l'activer :

```yaml
# config/settings.yaml
fmp:
  enabled: true
  api_key: "votre_cle"        # ou variable d'environnement FMP_API_KEY
```

Le module compte ses appels par jour et s'arrête au budget configuré
(200 par défaut, sous la limite de 250).

### Sources volontairement exclues

Aucun scraping de Boursorama, Investing.com, ZoneBourse ou de toute autre
plateforme dont les conditions d'utilisation l'interdisent. Seules les APIs
ci-dessus sont interrogées.

## L'écart de couverture Europe / États-Unis

C'est une **limite réelle du marché de la donnée financière gratuite**, pas un
choix d'implémentation :

- une société américaine dépose ses comptes à la SEC → historique long,
  structuré, officiel, gratuit ;
- une société européenne non cotée aux États-Unis n'a **aucun équivalent
  gratuit** → on se rabat sur Yahoo Finance, qui expose 4 à 5 exercices.

L'application applique une **fenêtre adaptative** et l'affiche :

- chaque titre porte un badge `fenêtre : N ans` ;
- en dessous de `window.min_years` (3 par défaut), le titre est **exclu du
  classement** et listé dans un tableau séparé « Données fondamentales
  incomplètes » avec le motif ;
- un titre dont la couverture des critères tombe sous
  `data_quality.min_weight_coverage` (70 %) est également exclu, plutôt que
  classé sur un score partiel donc trompeur.

Comparer un TCAM sur 4 ans à un TCAM sur 5 ans n'est pas strictement homogène.
Le badge est là pour le rappeler à chaque lecture.

## Méthodologie de scoring

Score composite = moyenne pondérée de 5 piliers. **Aucun poids n'est codé en
dur** : tout est dans `config/scoring.yaml`.

| Pilier | Poids | Critères |
|---|---|---|
| Croissance | 35 % | CAGR du chiffre d'affaires (40 %), CAGR du résultat net (35 %), évolution de la marge nette (25 %) |
| Valorisation | 25 % | PEG (40 %), P/E vs sa moyenne historique (35 %), P/B (15 %), P/E vs médiane du secteur (10 %) |
| Rentabilité | 20 % | ROE moyen (55 %), marge nette moyenne (45 %) |
| Qualité du bilan | 15 % | dette nette / EBITDA (60 %), ratio de liquidité générale (40 %) |
| Dividende | 5 % | rendement courant (50 %), régularité et croissance (50 %) |

Chaque critère est converti en sous-score 0–100 par une **fonction linéaire par
morceaux** définie dans la configuration :

```yaml
revenue_cagr:
  points: [[-0.10, 0], [0.0, 20], [0.05, 45], [0.10, 65], [0.20, 85], [0.35, 100]]
```

### Seuils absolus plutôt que rangs relatifs — et pourquoi

Un score fondé sur le rang dans l'univers analysé change quand la composition
de l'univers change, sans qu'aucun fondamental n'ait bougé : les alertes
« variation du score » deviendraient alors du bruit. Les barèmes sont donc
absolus. Seul le critère explicitement relatif (P/E vs médiane du secteur) est
calculé par rapport aux pairs de l'univers, et exige au moins 3 pairs
valorisables pour être significatif.

### Règles de prudence appliquées

- **Un critère non calculable est marqué n/d, jamais remplacé par une valeur
  favorable.** Un PEG sans croissance positive n'est pas un « bon » PEG : il
  n'est pas interprétable, et le critère est neutralisé.
- Un TCAM sur base négative ou nulle est refusé (aucun sens économique).
- Un ROE calculé sur des fonds propres négatifs est écarté, exercice par
  exercice, et signalé dans le détail.
- **Un titre sans dividende n'est pas pénalisé** : le pilier reçoit un score
  neutre (50/100 configurable). La détection repose sur les données (aucun
  dividende versé, aucun rendement, données de marché par ailleurs présentes),
  pas sur le libellé d'un message.
- Un pilier dont la couverture est inférieure à 50 % est neutralisé et son
  poids **redistribué** sur les autres.
- L'évolution de marge est lissée (moyenne des 2 premiers exercices vs moyenne
  des 2 derniers) dès que 4 exercices sont disponibles, pour ne pas dépendre
  d'un exercice exceptionnel.

### Deux pièges de données traités explicitement

1. **Divisions d'actions.** EDGAR restitue les données par action *telles que
   publiées à l'époque du dépôt*, et retraite les comparatifs dans les dépôts
   *postérieurs* à une division. Un BPA de 3,85 $ déposé avant une division
   10 pour 1 doit être ramené à 0,385 $ ; le même exercice republié après la
   division vaut déjà 0,385 $ et ne doit **surtout pas** être divisé deux fois.
   Le retraitement s'appuie donc sur la **date de dépôt** de chaque valeur, pas
   sur la date de clôture. Sans cela, la croissance du BPA et le P/E historique
   de NVIDIA, Apple ou Amazon sont faux d'un facteur 4 à 40.
2. **P/E historique.** Le cours utilisé est ajusté des divisions d'actions mais
   **pas** des dividendes (`auto_adjust=False`) : un cours réajusté des
   dividendes sous-estimerait mécaniquement les P/E passés.

## Fonctionnalités

- **Classement** — tableau trié par score décroissant, sous-scores par pilier en
  colonnes, export CSV, bouton « Relancer l'analyse maintenant ». Sélection du
  titre pour voir le détail critère par critère : valeur brute, sous-score,
  poids et explication du calcul.
- **Watchlist** — ajout manuel de tickers, graphique de cours 5 ans, détail des
  critères, exercices retenus avec la source de chaque champ, calendrier de
  publication.
- **Alertes** — par titre : franchissement de seuil de cours (haut/bas),
  nouvelle publication de résultats, variation du score composite au-delà d'un
  seuil, entrée ou sortie du top N. Envoi par notification locale
  (`data/notifications.log` + notification de bureau) et/ou email SMTP.
- **Historique des scores** — évolution du score et du rang dans le temps,
  historique par critère, stocké en SQLite local.

## Exécution périodique

**Linux / macOS (cron)** — tous les jours à 19 h en semaine :

```cron
0 19 * * 1-5 cd /chemin/vers/Trading-assistant && .venv/bin/python scripts/run_screening.py --quiet >> data/cron.log 2>&1
```

**Windows (Tâches planifiées)** — action : démarrer un programme
`C:\chemin\.venv\Scripts\python.exe`, arguments `scripts\run_screening.py --quiet`,
répertoire de départ `C:\chemin\Trading-assistant`.

**Sans planificateur système** :

```bash
python scripts/scheduler.py --hour 19 --minute 30 --days mon-fri
```

## Alertes par email

```yaml
alerts:
  email_enabled: true
  email:
    smtp_host: "smtp.exemple.fr"
    smtp_port: 587          # 465 = SSL direct, sinon STARTTLS
    username: "vous@exemple.fr"
    password: "mot_de_passe_application"   # jamais le mot de passe principal
    sender: "vous@exemple.fr"
    recipients: ["vous@exemple.fr"]
```

Chaque email porte le rappel que l'alerte constate un franchissement de seuil
que vous avez défini vous-même, et ne constitue pas une incitation à agir.

## Limites connues

- **yfinance est une bibliothèque non officielle** qui interroge les points
  d'entrée internes de Yahoo Finance. Elle peut cesser de fonctionner sans
  préavis si Yahoo modifie son service. Aucun engagement de disponibilité ni
  d'exactitude. Usage strictement personnel.
- Si la couche TLS de yfinance (`curl_cffi`) est bloquée — proxy d'entreprise
  qui re-termine le TLS — l'application bascule automatiquement sur une session
  `requests` classique. Ce repli couvre les deux modes d'échec : exception TLS,
  et réponse vide sans exception (yfinance journalise alors `possibly delisted`
  à tort). Forçable via `yahoo.force_requests_session: true`.
- **Banques et assurances** : la dette nette / EBITDA et le ratio de liquidité
  générale n'ont pas de sens pour ces modèles d'activité. Le pilier « qualité
  du bilan » est généralement neutralisé pour ces titres et son poids
  redistribué — c'est signalé dans le détail, mais leurs scores restent moins
  comparables à ceux des sociétés industrielles.
- **Exercices décalés** : l'exercice est rattaché à l'année civile où tombe la
  majorité de la période (clôture de janvier à mai → année précédente). Les
  dividendes issus de Yahoo sont agrégés par année **civile** : pour une société
  clôturant en juin, l'appariement avec l'exercice comptable est approximatif.
  L'année civile en cours, incomplète par construction, est exclue du calcul de
  croissance du dividende — sans quoi tout titre paraîtrait baisser son
  dividende.
- **Sociétés en perte** : lorsqu'une société affiche des pertes, le P/E est
  négatif et le PEG n'est pas interprétable — trois des quatre critères de
  valorisation deviennent alors indisponibles, et la couverture tombe souvent
  sous le seuil de 70 %. Ces titres sont donc **exclus du classement** plutôt
  que notés sur des critères manquants. C'est rigoureux, mais cela écarte de
  fait certaines valeurs de croissance non encore rentables. Pour les inclure,
  deux réglages possibles dans `config/scoring.yaml` : abaisser
  `data_quality.min_weight_coverage`, ou réduire le poids du critère `peg_ratio`
  au profit de `price_to_book` (qui reste calculable en cas de perte).
- Les données ne sont **pas auditées**. Une erreur de source se propage au
  score. Le détail par critère est là pour vous permettre de la repérer.
- **Durée d'exécution** : environ 5 à 6 secondes par titre sans cache, soit
  ~15 minutes pour 140 titres avec 4 requêtes en parallèle. Un cache disque de
  12 h (configurable) évite de reconsommer les quotas à chaque rafraîchissement
  d'écran. Sous forte parallélisation, Yahoo renvoie parfois un contenu tronqué
  avec un code HTTP 200 : ces réponses sont détectées, réessayées et **jamais**
  mises en cache.

## Architecture

```
config/            scoring.yaml (poids, barèmes), universes.yaml, settings.example.yaml
src/investassist/
  config.py        chargement typé des configurations
  disclaimers.py   source unique des avertissements de non-conseil
  models.py        structures normalisées (Snapshot, AnnualRecord, StockScore…)
  providers/
    base.py        limitation de débit, cache disque, HTTP avec reprise
    edgar.py       SEC companyfacts : ticker→CIK, sélection des exercices
    yahoo.py       yfinance : cours, ratios, dividendes, divisions d'actions
    fmp.py         optionnel — contrôle croisé, budget d'appels journalier
  fundamentals.py  consolidation multi-source, traçabilité par champ, splits
  criteria.py      calcul des critères (valeur + explication + motif d'absence)
  scoring.py       sous-scores, piliers, composite, exclusions
  screener.py      orchestration d'univers en deux passes
  storage.py       SQLite : historique, watchlist, règles et journal d'alertes
  alerts/          évaluation des règles, notification locale et SMTP
  ui/              composants Streamlit (dont la bannière d'avertissement)
app.py             tableau de bord (5 vues)
scripts/           validate_ticker.py, run_screening.py, scheduler.py
tests/             58 tests hors ligne, fixtures figées
```

## Tests

```bash
python -m pytest tests/ -q
```

58 tests, **aucun appel réseau** : fixtures EDGAR figées (dont un cas de
comparatif retraité après division d'actions), cas limites des critères
(base négative, EBITDA négatif, fonds propres négatifs, PEG non interprétable),
règles d'exclusion, stockage SQLite, et vérification que chaque vue de
l'interface affiche l'avertissement de non-conseil.
