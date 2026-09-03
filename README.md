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

### Le plus simple : l'exécutable Windows

1. Téléchargez `Investassist-windows.zip` depuis la page **Releases** du dépôt.
2. Décompressez-le dans un dossier de votre choix — le Bureau, une clé USB,
   peu importe.
3. Double-cliquez sur `Investassist.exe`. Un écran de démarrage s'affiche
   pendant le chargement, puis le navigateur ouvre l'application.

Aucune installation de Python, aucune dépendance : le fichier contient tout.
Si Windows affiche « Windows a protégé votre ordinateur », cliquez sur
**Informations complémentaires** puis **Exécuter quand même** — le fichier vient
d'Internet, Windows demande une confirmation.

### Icône sur le Bureau

Double-cliquez **une fois** sur `Creer-un-raccourci-sur-le-Bureau.bat` : une
icône Investassist apparaît sur votre Bureau et lance l'application
directement. Le script est un simple raccourci Windows — rien n'est installé,
rien n'est écrit dans le registre.

### Ce que vous voyez au démarrage

Un exécutable « un seul fichier » se déballe pendant une dizaine de secondes au
premier lancement. Trois retours visuels comblent cette attente, pour qu'aucun
moment ne donne l'impression que rien ne se passe :

1. **Écran de démarrage** — logo, nom et message d'avancement, affichés dès le
   double-clic par le lanceur de PyInstaller.
2. **Fenêtre de console** — animation de progression pendant le démarrage du
   serveur, puis l'adresse et les chemins effectifs.
3. **Écran d'attente dans le navigateur** — présent dans le HTML lui-même, donc
   affiché avant même l'exécution du script : aucune page blanche. Les
   animations s'effacent si le système signale une préférence pour un mouvement
   réduit.

### L'application est portable

Au premier lancement, deux dossiers apparaissent **à côté** de l'exécutable :

```
MonDossier/
├── Investassist.exe
├── config/      vos réglages : pondérations, barèmes, univers analysés
└── donnees/     votre historique, votre watchlist, vos alertes, le cache
```

**Copier ce dossier entier sur une autre machine suffit** à y retrouver l'outil
dans le même état : même historique de scores, même watchlist, mêmes règles
d'alerte. Rien n'est écrit ailleurs sur l'ordinateur, aucune entrée de registre,
aucune installation.

Si l'emplacement est en lecture seule (CD, partage réseau verrouillé),
l'application bascule automatiquement sur le dossier utilisateur et l'indique
au démarrage.

### Réglage conseillé au premier lancement

Ouvrez `config/settings.yaml` avec le Bloc-notes et remplacez l'adresse email de
la ligne `user_agent`. L'API publique de la SEC l'exige pour fournir les données
officielles des sociétés américaines — sans elle, l'analyse se limite à Yahoo
Finance, soit quatre exercices au lieu de cinq. Cette adresse n'est transmise
qu'à la SEC.

### Depuis les sources (développement)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python lanceur.py
```

Ou double-cliquez sur `start.bat` (Windows) / `start.sh` (macOS, Linux) : ils
créent l'environnement, installent les dépendances puis lancent l'application.

### Construire l'exécutable soi-même

```bash
pip install pyinstaller
pyinstaller investassist.spec        # produit dist/Investassist.exe
```

PyInstaller ne sait pas produire un binaire Windows depuis Linux ou macOS : la
construction doit tourner sur Windows. Le workflow
`.github/workflows/executable.yml` s'en charge sur une machine Windows fournie
par GitHub, éprouve l'exécutable produit (démarrage réel, interface servie,
API protégée) et le publie en téléchargement.

## Publier vos modifications vers votre dépôt GitHub

### Comprendre le circuit

Il n'existe que **deux emplacements**, pas trois. GitHub Desktop n'est pas une
copie supplémentaire de votre dépôt : c'est une télécommande graphique pour Git,
au même titre que `publier.bat`.

```
   VOTRE PC                                  GITHUB (en ligne)
   ┌─────────────────────────┐              ┌──────────────────────┐
   │ Bureau\Trading-assistant │ ── Push ──► │ Llegender/           │
   │ (dossier de travail)     │ ◄── Pull ── │ Trading-assistant    │
   └─────────────────────────┘              └──────────────────────┘
        ▲
        └── GitHub Desktop ou publier.bat : deux façons de piloter la MÊME flèche
```

- On **clone une seule fois**. Ce dossier devient le dossier de travail
  permanent : inutile de retélécharger un ZIP par la suite.
- La synchronisation existe, mais elle est **déclenchée par vous** : `Push`
  envoie vos modifications, `Pull` récupère ce qui a changé en ligne. Rien ne
  bouge automatiquement.
- L'envoi va **directement** de votre dossier vers GitHub. Il n'y a aucune étape
  intermédiaire entre « version bureau » et « version en ligne ».

Le dossier `donnees/` n'est jamais publié : sur deux ordinateurs, le code se
synchronise par Git, mais l'historique de scores et la watchlist se transportent
en copiant ce dossier (clé USB), comme le reste de l'application portable.

### Le script

Vous travaillez dans un dossier local, vous modifiez des pondérations ou des
univers, et vous voulez conserver ces changements dans votre dépôt : double-
cliquez sur **`publier.bat`** (Windows) ou lancez `./publier.sh`.

Le script fonctionne même si le dossier vient d'un ZIP et n'a jamais été relié
à Git : il récupère l'historique du dépôt distant, l'adopte sans toucher à vos
fichiers, puis publie l'état actuel du dossier comme une nouvelle mise à jour.

```
  Dépôt GitHub [https://github.com/Llegender/Trading-assistant.git] :
  Branche [main] :
  4 fichier(s) modifié(s) :
    M  config/scoring.yaml
    M  config/universes.yaml
  Message du commit [Mise à jour du 28/08/2026 06:15] :
```

### Ce qui arrive aux fichiers déjà présents sur le dépôt

Le comportement diffère selon l'origine du dossier — vérifié par exécution sur
un dépôt réel :

| Origine du dossier | Effet sur le dépôt |
|---|---|
| **Dossier cloné** (`git clone`) | **Fusion.** Un fichier ajouté entre-temps depuis un autre poste est conservé. Seul ce que vous modifiez change. |
| **Dossier issu d'un ZIP** | **Miroir.** Le dépôt reflète exactement votre dossier : un fichier présent sur le dépôt mais absent du dossier serait supprimé. |

Dans les deux cas, **l'historique conserve tout** : une version supprimée reste
récupérable dans les commits précédents. Ce qui s'accumule, ce sont les commits
— jamais des doublons de fichiers.

Parce que le second cas peut faire perdre du travail, le script **refuse de
publier** dès qu'une suppression est en jeu : il liste les fichiers concernés et
attend votre accord explicite. Sans confirmation, rien n'est envoyé.

```
  ATTENTION — 3 fichier(s) présent(s) sur le dépôt
  seraient SUPPRIMÉS, car absents de ce dossier :
    ✕ README.md
    ✕ dossier/module.py
    ✕ fichier-important.txt

  Publication annulée : rien n'a été envoyé.
```

La voie recommandée reste donc le clone : `git clone` une fois, puis vous
travaillez dedans et publiez autant de fois que vous voulez.

Ce qui n'est **jamais** publié : le dossier `donnees/` (votre base, votre
watchlist, votre cache) et `config/settings.yaml` (vos identifiants SMTP). Le
script complète `.gitignore` au besoin.

Aucun mot de passe ni jeton n'est enregistré : l'authentification est confiée au
gestionnaire d'identifiants de Git. Prérequis : Git installé
([git-scm.com](https://git-scm.com/download/win)) ou GitHub Desktop, qui gère la
connexion pour vous.

## Utilisation

```bash
# Application complète (interface web servie localement)
python lanceur.py

# Valider UN titre de bout en bout, avec le détail de chaque critère
python scripts/validate_ticker.py MSFT
python scripts/validate_ticker.py AIR.PA --no-cache

# Analyse complète en ligne de commande, sans interface
python scripts/run_screening.py --universes cac40,nasdaq100
```

L'application affiche **le dernier classement enregistré** dès son ouverture, et
un instantané est livré avec l'exécutable : vous voyez donc un classement daté
immédiatement, sans attendre une première analyse de huit minutes.

`validate_ticker.py` affiche chaque donnée brute récupérée, chaque critère,
son sous-score et le détail du calcul. C'est l'outil à utiliser en premier :
une anomalie de parsing s'y voit sur 3 appels réseau, au lieu d'être noyée
dans une exécution de 140 titres.

## Sources de données — état vérifié le 2 septembre 2026

Les plans gratuits évoluent souvent. Les limites ci-dessous ont été **testées
par appel réel**, pas reprises d'une documentation.

| Source | Clé | Vérifié | Ce qu'elle apporte réellement |
|---|---|---|---|
| **SEC EDGAR** (`data.sec.gov`, API XBRL `companyfacts`) | aucune | HTTP 200, illimité | **≥ 6 exercices annuels** exploitables : CA, résultat net, résultat opérationnel, amortissements, capitaux propres, dette, trésorerie, actifs/passifs courants, BPA dilué, dividende déclaré. Sociétés déposant aux **États-Unis** uniquement. Limite officielle : 10 requêtes/s, User-Agent identifiant obligatoire. |
| **Yahoo Finance** via `yfinance` | aucune | fonctionnel | Cours (5 ans), capitalisation, P/E, P/B, ROE, rendement, secteur, calendrier de publication, dividendes et divisions d'actions. Couvre l'Europe. **Mais seulement 4 à 5 exercices** dans les états financiers annuels. |
| **Dépôts ESEF européens** (`filings.xbrl.org`) | aucune | HTTP 200, 25 892 dépôts / 27 pays | Rapports annuels officiels en XBRL des sociétés cotées dans l'Union, depuis l'exercice 2020. **Trois exercices par dépôt**, comparatifs inclus : chiffre d'affaires, résultat net, résultat brut, capitaux propres, total de l'actif, actifs/passifs courants, trésorerie, emprunts, flux d'exploitation, BPA. Seules les balises IFRS **normalisées** sont lues, jamais les extensions propres à l'émetteur. |
| **Financial Modeling Prep** | requise (gratuite) | 250 req/jour | Actions **US uniquement** (le global est payant), 5 ans de cours, **5 trimestres** d'états financiers, 500 Mo/30 jours. Les points d'entrée `/api/v3/` sont en fin de vie au profit de `/stable/`. |

### Pourquoi FMP n'est qu'une source d'appoint — et à quoi il sert quand même

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

Historiquement le point faible du projet : Yahoo Finance ne remonte que
**quatre exercices** pour les titres européens, contre cinq via EDGAR pour les
américains. Conséquence mesurée sur un classement CAC 40 + Nasdaq-100 : les
vingt premières places étaient **toutes** américaines, la meilleure valeur
européenne arrivant 24ᵉ. Comparer un TCAM sur 4 ans à un TCAM sur 5 ans n'est
pas homogène, et une partie de l'écart était donc mécanique.

**Il existe un équivalent européen d'EDGAR, gratuit.** Depuis l'exercice 2020,
toute société cotée sur un marché réglementé de l'Union publie son rapport
annuel au format électronique unique européen (ESEF), c'est-à-dire en XBRL
selon la taxonomie IFRS. L'autorité XBRL en tient un index public :
[filings.xbrl.org](https://filings.xbrl.org/).

Vérifié le 2 septembre 2026 : 25 892 dépôts indexés sur 27 pays, dont 1 178
français ; aucune clé d'API ; aucun quota annoncé. Et surtout, **un dépôt livre
trois exercices d'un coup**, comparatifs inclus.

L'application s'en sert pour allonger l'historique européen, avec deux règles
de prudence :

- Yahoo reste maître sur les exercices qu'il couvre déjà, ESEF ne fait que
  **remonter plus loin dans le passé** ;
- l'exercice commun aux deux sources sert de **contrôle de concordance**. Une
  divergence de plus de 5 % sur le chiffre d'affaires révèle deux périmètres
  de consolidation différents : l'apport ESEF est alors écarté, car mieux vaut
  un historique court qu'un taux de croissance faux. Sur LVMH, les deux
  sources concordent au million près (CA 2022 : 79,184 Md€ des deux côtés).

Limites de cette source, assumées :

- les sociétés étendent la taxonomie pour leurs sous-totaux propres. Le
  résultat d'exploitation de LVMH est ainsi une balise maison. On ne lit donc
  **que les balises IFRS normalisées**, jamais les extensions, dont le sens
  varie d'un émetteur à l'autre — ce poste reste pris chez Yahoo ;
- les banques tagguent leur compte de résultat autrement (produits d'intérêts
  et non chiffre d'affaires) : l'apport ESEF est nul pour elles ;
- un fichier de faits pèse environ 5 Mo car il contient le texte des annexes.
  Comptez ce volume **une seule fois par société européenne** : un exercice
  publié ne change plus jamais, le cache est donc permanent. Désactivable par
  `esef.enabled: false` dans `config/settings.yaml`.

La table `config/esef.yaml` fait le lien entre ticker et raison sociale du
déposant — l'index ESEF n'indexe pas les tickers. Les 42 valeurs du CAC 40 y
sont vérifiées une à une.

L'application applique par ailleurs une **fenêtre adaptative** et l'affiche :

- chaque titre porte un badge `fenêtre : N ans` ;
- en dessous de `window.min_years` (3 par défaut), le titre est **exclu du
  classement** et listé dans un tableau séparé « Données fondamentales
  incomplètes » avec le motif ;
- un titre dont la couverture des critères tombe sous
  `data_quality.min_weight_coverage` (70 %) est également exclu, plutôt que
  classé sur un score partiel donc trompeur.

Le badge reste là pour rappeler, à chaque lecture, sur combien d'exercices le
score a réellement été calculé.

## Méthodologie de scoring

Score composite = moyenne pondérée de 5 piliers. **Aucun poids n'est codé en
dur** : tout est dans `config/scoring.yaml`.

| Pilier | Poids | Critères |
|---|---|---|
| Croissance | 35 % | CAGR du chiffre d'affaires (40 %), CAGR du résultat net (35 %), évolution de la marge nette (25 %) |
| Valorisation | 25 % | PEG (25 %), rendement du free cash flow (25 %), P/E vs sa **médiane** historique (22 %), VE / chiffre d'affaires (13 %), P/B (10 %), P/E vs médiane du secteur (5 %) |
| Rentabilité | 20 % | ROCE (30 %), ROE moyen (20 %), marge nette moyenne (20 %), conversion du bénéfice en trésorerie (18 %), marge brute moyenne (12 %) |
| Qualité du bilan | 15 % | dette nette / EBITDA (30 %), fonds propres / total de l'actif (25 %), couverture des intérêts (20 %), ratio de liquidité générale (15 %), évolution du nombre d'actions (10 %) |
| Dividende | 5 % | rendement courant (50 %), régularité et croissance (50 %) |

**Le poids des cinq piliers n'a pas bougé** ; seule la composition interne a
changé, pour couvrir la trésorerie, la rentabilité du capital et la dilution.
Trois choix méritent d'être explicités :

- **le ROE passe de 55 % à 20 % du pilier rentabilité**, au profit du ROCE.
  Le ROE rapporte le bénéfice aux seuls fonds propres : une société qui
  s'endette pour racheter ses actions l'améliore mécaniquement sans rien
  améliorer de son exploitation. Le capital employé inclut la dette, donc la
  performance ne peut plus être fabriquée par le levier. Le ROIC aurait été
  préférable en théorie, mais il exige un taux d'impôt effectif qu'aucune
  source gratuite ne publie de façon fiable : le poser par convention
  reviendrait à inventer une donnée.
- **la conversion du bénéfice en trésorerie** (free cash flow / résultat net)
  est le seul contrôle de qualité des bénéfices possible : le résultat
  comptable se pilote, les encaissements beaucoup moins.
- **le P/E historique est comparé à la médiane, non à la moyenne.** Un exercice
  à bénéfice quasi nul produit un P/E de plusieurs centaines qui tire la
  moyenne vers le haut et fait passer le titre pour bon marché. Mesuré sur le
  CAC 40 + Nasdaq-100 : neuf titres portaient une valeur aberrante supérieure
  à cinq fois la médiane de leur propre série — CoStar affichait 3 362 sur un
  exercice, pour une médiane de 107, et obtenait 100/100 de « décote ». Le
  passage à la médiane déplace 49 titres sur 120 de cinq points ou plus sur ce
  critère, presque toujours à la baisse : la moyenne biaisait le pilier
  valorisation vers l'optimisme.

### Pertinence sectorielle et conditions préalables

Un critère peut être déclaré **sans objet** plutôt que manquant, ce qui
redistribue son poids sans peser sur la couverture de données :

```yaml
net_debt_to_ebitda:
  sectors_excluded: ["Financial Services"]   # la dette EST sa matière première
  sector_points:
    Real Estate: [[0.0, 100], [5.0, 70], [9.0, 28], [13.0, 0]]

peg_ratio:
  requires: ["benefice_positif"]             # sans bénéfice, pas de P/E
```

La distinction est la clé de voûte de la lecture : une lacune de données doit
peser sur la couverture, une non-pertinence non. Sans elle, on pénalisait une
banque pour ne pas être une entreprise industrielle, et on excluait du
classement toute société de croissance pas encore rentable.

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

- **Classement** — tableau trié, filtrable par zone, secteur et recherche
  textuelle ; sous-scores par pilier en colonnes, micro-courbe de tendance, et
  bouton **Lancer l'analyse maintenant** avec avancement titre par titre.
- **Fiche par titre** — sous-scores des cinq piliers, puis chaque critère avec
  sa valeur, son sous-score et l'explication du calcul ; courbe d'évolution du
  score composite d'une analyse à l'autre.
- **Watchlist** — enregistrée dans le dossier de l'application, donc elle vous
  suit d'un ordinateur à l'autre. Un titre absent du dernier classement est
  analysé à la demande, sans relancer l'univers entier.
- **Alertes** — par titre : franchissement de seuil de cours (haut/bas),
  nouvelle publication de résultats, variation du score composite au-delà d'un
  seuil, entrée ou sortie du top N. Envoi par notification locale
  (`data/notifications.log` + notification de bureau) et/ou email SMTP.
  Une alerte se déclenche au **franchissement**, pas tant que la condition
  reste vraie : une règle « cours au-dessous de 300 » notifie une fois, puis
  se réarme seulement si le cours repasse au-dessus. Les tickers et les seuils
  sont vérifiés à la création de la règle, pour éviter une alerte qui ne
  partirait jamais à cause d'une faute de frappe.
- **Historique des scores** — évolution du score et du rang dans le temps,
  stockée en SQLite local.
- **Aucune donnée ne sort de la machine** hormis les appels aux sources
  financières. Le serveur n'écoute que sur `127.0.0.1` et exige un jeton tiré au
  hasard à chaque démarrage : une page web ouverte dans un autre onglet ne peut
  pas piloter l'application.

## Exécution périodique

**Linux / macOS (cron)** — tous les jours à 19 h en semaine :

```cron
0 19 * * 1-5 cd /chemin/vers/Trading-assistant && .venv/bin/python scripts/run_screening.py --quiet >> data/cron.log 2>&1
```

**Windows (Tâches planifiées)** — action : démarrer un programme
`C:\chemin\.venv\Scripts\python.exe`, arguments `scripts\run_screening.py --quiet`,
répertoire de départ `C:\chemin\Trading-assistant`.

Après une exécution planifiée, ouvrez simplement l'application : elle réaffiche
le dernier classement enregistré sans rien recalculer.

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
- **Banques, assurances et foncières** : la dette nette / EBITDA, le ratio de
  liquidité générale et la couverture des intérêts n'ont pas de sens pour une
  banque — la dette EST sa matière première. Ces critères sont donc déclarés
  **sans objet** pour le secteur (et non « manquants » : une non-pertinence ne
  doit pas peser sur la couverture de données), leur poids est redistribué, et
  le levier est mesuré par les fonds propres rapportés au total de l'actif, sur
  un barème propre au secteur — 5 % de fonds propres est la norme
  réglementaire d'une banque, pas une fragilité. Même principe pour
  l'immobilier : sept fois l'EBITDA de dette nette est alarmant dans
  l'industrie et banal pour une foncière, dont l'actif est un immeuble financé
  par dette longue. Sans ce barème dédié, Unibail-Rodamco était noté 23/100
  sur son bilan pour une structure de capital normale dans son métier.
  Ces réglages sont dans `config/scoring.yaml` (`sectors_excluded`,
  `sector_points`) — rien n'est codé en dur.
- **Exercices décalés** : l'exercice est rattaché à l'année civile où tombe la
  majorité de la période (clôture de janvier à mai → année précédente). Les
  dividendes issus de Yahoo sont agrégés par année **civile** : pour une société
  clôturant en juin, l'appariement avec l'exercice comptable est approximatif.
  L'année civile en cours, incomplète par construction, est exclue du calcul de
  croissance du dividende — sans quoi tout titre paraîtrait baisser son
  dividende.
- **Sociétés en perte** : une société sans bénéfice n'a pas de P/E, donc ni
  PEG, ni P/E historique, ni P/E sectoriel. Ces trois critères comptaient
  auparavant comme autant de **lacunes**, ce qui suffisait à neutraliser le
  pilier valorisation et à exclure du classement toute valeur de croissance
  pas encore rentable — CrowdStrike, Zscaler, MongoDB, Atlassian et Intel
  disparaissaient ainsi de l'analyse. Ils sont désormais déclarés **sans
  objet** (condition `requires: ["benefice_positif"]` dans
  `config/scoring.yaml`), et le pilier reste calculé sur le rendement du free
  cash flow et la valeur d'entreprise rapportée au chiffre d'affaires, deux
  critères qui gardent un sens sans bénéfice comptable. Le levier bascule de
  la même façon sur le free cash flow quand l'EBITDA est négatif : c'est le
  cas exact des éditeurs de logiciels, dont la rémunération en actions creuse
  le résultat comptable alors que la trésorerie rentre.
- **Fonds propres négatifs** : des rachats d'actions supérieurs aux bénéfices
  accumulés rendent les fonds propres comptables négatifs (Starbucks,
  McDonald's, Philip Morris). Le ROE et le P/B perdent alors tout sens. Le
  message d'origine annonçait « fonds propres absents », ce qui était faux et
  laissait croire à un défaut de la source : il nomme maintenant la vraie
  cause, et la rentabilité est mesurée par le **ROCE**, calculable sur un
  capital employé même quand les fonds propres sont négatifs.
- Les données ne sont **pas auditées**. Une erreur de source se propage au
  score. Le détail par critère est là pour vous permettre de la repérer.
- **Durée d'exécution** : mesurée à **478 secondes pour 143 titres** (CAC 40 +
  Nasdaq-100) sans cache, soit environ 3,3 secondes par titre avec 4 requêtes
  en parallèle ; **42 secondes** avec le cache chaud. Le premier passage
  télécharge en plus les dépôts ESEF des valeurs européennes (environ 5 Mo par
  société, une seule fois). Un cache disque de
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
  export.py        format JSON publie vers le site statique
app.py             tableau de bord Streamlit local (5 vues)
web/               site statique Netlify (HTML/CSS/JS sans dependance)
  data/            donnees publiees par l'analyse planifiee
netlify.toml       publication du dossier web/, en-tetes, fonction edge
netlify/edge-functions/auth.ts   protection par mot de passe
.github/workflows/analyse.yml    analyse planifiee et publication
scripts/           validate_ticker.py, run_screening.py, build_site.py, scheduler.py
tests/             108 tests hors ligne, fixtures figées
```

## Tests

```bash
python -m pytest tests/ -q
```

114 tests, **aucun appel réseau** : fixtures EDGAR figées (dont un cas de
comparatif retraité après division d'actions), cas limites des critères
(base négative, EBITDA négatif, fonds propres négatifs, PEG non interprétable),
règles d'exclusion, non-répétition des alertes, intégrité des univers,
restauration du dernier classement depuis SQLite, format d'export, et
serveur local démarré pour de vrai sur un port libre puis interrogé en HTTP
(routage, protection par jeton, traversée de répertoire). L'avertissement de
non-conseil est vérifié dans le HTML servi, indépendamment du JavaScript.

### Pièges vérifiés par des tests de non-régression

- Un ticker non quoté dans `universes.yaml` parmi `ON`, `OFF`, `YES`, `NO`,
  `Y`, `N` est lu par YAML comme un **booléen**. Le ticker `ON`
  (ON Semiconductor, Nasdaq-100) disparaissait ainsi de l'analyse. Gardez les
  guillemets ; un test vérifie cette contrainte sur tout le fichier.
- Une alerte de seuil ne doit se déclencher qu'au **franchissement**, jamais
  tant que la condition reste vraie.
- Les données par action d'EDGAR doivent être retraitées des divisions
  d'actions selon la date de **dépôt**, jamais deux fois.
