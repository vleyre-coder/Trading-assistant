"""Source unique de verite pour les avertissements de non-conseil.

Toute interface (Streamlit, CLI, email d'alerte) doit reutiliser ces
constantes plutot que de reformuler : cela garantit qu'aucun ecran ne
puisse etre publie sans avertissement, et qu'une modification du texte
se propage partout.
"""

# Avertissement principal, obligatoire sur tout ecran affichant un
# classement, un score ou une alerte.
MAIN = (
    "Classement calcule a partir de donnees fondamentales historiques et "
    "actuelles. Ne constitue pas un conseil en investissement ni une "
    "prediction de performance future."
)

MAIN_HTML = (
    "Classement calculé à partir de données fondamentales historiques et "
    "actuelles. Ne constitue pas un conseil en investissement ni une "
    "prédiction de performance future."
)

# Rappel de ce que le classement mesure — et ne mesure pas.
WHAT_THIS_IS = (
    "Ce classement mesure l'ADÉQUATION AUX CRITÈRES FONDAMENTAUX au moment "
    "du calcul : croissance, rentabilité, qualité de bilan, valorisation "
    "relative. Un titre en tête de classement présente de bons fondamentaux "
    "**selon les données disponibles aujourd'hui** ; cela n'indique ni le "
    "moment, ni la direction, ni la certitude d'un mouvement de cours."
)

WHAT_THIS_IS_NOT = (
    "Ce n'est pas une recommandation d'achat ou de vente, pas une prédiction "
    "de prix, pas un conseil personnalisé. Les données proviennent de sources "
    "gratuites pouvant être incomplètes, retardées ou erronées, et ne sont "
    "pas auditées."
)

DATA_LIMITS = (
    "Sources : Yahoo Finance via la bibliothèque non officielle yfinance "
    "(peut cesser de fonctionner sans préavis), SEC EDGAR (sociétés cotées "
    "aux États-Unis uniquement). La couverture fondamentale gratuite est "
    "structurellement plus pauvre en Europe qu'aux États-Unis : la fenêtre "
    "d'analyse réellement utilisée est affichée pour chaque titre."
)

# Pied de page des emails / notifications d'alerte.
ALERT_FOOTER = (
    "---\n"
    "Alerte generee automatiquement par votre outil personnel Investassist.\n"
    + MAIN
    + "\nUne alerte signale un franchissement de seuil que vous avez defini "
    "vous-meme. Elle ne constitue en aucun cas une incitation a acheter ou "
    "a vendre."
)


def ranking_phrasing(ticker: str, rank: int) -> str:
    """Formulation neutre imposee pour presenter un rang.

    Jamais "achetez X" ni "X va monter" : toujours le rang et les criteres.
    """
    return f"{ticker} est classé n°{rank} sur les critères fondamentaux retenus — détail ci-dessous."
