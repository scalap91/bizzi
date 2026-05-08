"""
tools/regions/region_detector.py
=================================
Détecte la région française d'un article à partir de villes/départements/régions
mentionnés dans le titre + contenu.

Usage :
    from tools.regions.region_detector import detect_region_by_content
    region_name = detect_region_by_content(title, content)  # ou None si international/flou
"""
import re
import unicodedata


def _norm(s: str) -> str:
    """Normalise (sans accents, minuscules)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


# Mots-clés région (nettoyés des ambigus : nord, loire-fleuve, var, vienne, champagne, etc.)
REGION_KEYWORDS = {
    "Ile-de-France": [
        "paris", "ile-de-france", "ile de france", "francilien", "parisien",
        "versailles", "boulogne-billancourt", "creteil", "nanterre",
        "argenteuil", "aulnay-sous-bois", "aubervilliers",
        "evry", "cergy", "pontoise", "mantes-la-jolie", "meaux", "melun",
        "fontainebleau", "saint-germain-en-laye", "sevran", "noisy-le-grand",
        "hauts-de-seine", "seine-saint-denis", "val-de-marne",
        "yvelines", "val-d'oise", "val d'oise", "seine-et-marne",
    ],
    "PACA": [
        "marseille", "aix-en-provence", "aix en provence", "toulon", "nice",
        "cannes", "antibes", "avignon", "hyeres", "frejus", "saint-raphael",
        "grasse", "cavaillon", "arles", "salon-de-provence", "digne",
        "manosque", "briancon", "menton", "vitrolles",
        "martigues", "istres", "carpentras", "aubagne",
        "bouches-du-rhone", "vaucluse", "alpes-maritimes",
        "hautes-alpes", "alpes-de-haute-provence",
        "provence", "cote d'azur", "camargue", "luberon",
    ],
    "Auvergne-Rhône-Alpes": [
        "lyon", "grenoble", "saint-etienne", "clermont-ferrand", "annecy",
        "chambery", "valence", "montlucon", "vichy", "aurillac", "roanne",
        "bourg-en-bresse", "le puy-en-velay", "privas", "annemasse",
        "villeurbanne", "venissieux", "lyonnais", "stephanois",
        "puy-de-dome", "haute-savoie", "ardeche", "allier", "cantal", "haute-loire",
        "auvergne", "dauphine", "beaujolais",
    ],
    "Occitanie": [
        "toulouse", "montpellier", "nimes", "perpignan", "beziers", "albi",
        "narbonne", "carcassonne", "tarbes", "sete", "cahors", "castres",
        "foix", "rodez", "millau", "lourdes", "pamiers", "agde", "toulousain",
        "haute-garonne", "herault", "pyrenees-orientales", "aude",
        "aveyron", "lozere", "hautes-pyrenees", "tarn-et-garonne",
        "languedoc", "roussillon", "cevennes",
    ],
    "Nouvelle-Aquitaine": [
        "bordeaux", "limoges", "poitiers", "pau", "la rochelle", "bayonne",
        "biarritz", "niort", "angouleme", "perigueux", "brive-la-gaillarde",
        "mont-de-marsan", "agen", "dax", "anglet", "saintes", "bordelais",
        "gironde", "charente-maritime", "dordogne",
        "pyrenees-atlantiques", "lot-et-garonne", "landes",
        "deux-sevres", "haute-vienne", "creuse", "correze",
        "aquitaine", "perigord", "gascogne", "pays basque", "bearn",
    ],
    "Hauts-de-France": [
        "lille", "amiens", "roubaix", "tourcoing", "arras", "lens",
        "boulogne-sur-mer", "calais", "dunkerque", "saint-omer", "beauvais",
        "compiegne", "soissons", "valenciennes", "douai", "cambrai",
        "maubeuge", "abbeville", "chantilly", "lillois",
        "pas-de-calais", "hauts-de-france",
        "picardie", "flandre", "artois",
    ],
    "Grand-Est": [
        "strasbourg", "metz", "nancy", "reims", "mulhouse", "colmar",
        "troyes", "charleville-mezieres", "epernay", "saint-dizier",
        "verdun", "thionville", "epinal", "haguenau", "selestat", "chalons-en-champagne",
        "bas-rhin", "haut-rhin", "moselle", "meurthe-et-moselle",
        "vosges", "haute-marne", "ardennes",
        "alsace", "lorraine", "alsacien", "lorrain",
    ],
    "Bretagne": [
        "rennes", "brest", "quimper", "lorient", "vannes", "saint-brieuc",
        "saint-malo", "lannion", "concarneau", "morlaix", "dinan", "auray",
        "fougeres", "vitre", "lanester", "ploemeur", "breton",
        "finistere", "morbihan", "cotes-d'armor", "ille-et-vilaine",
        "armorique", "broceliande", "bretagne",
    ],
    "Pays-de-la-Loire": [
        "nantes", "angers", "le mans", "saint-nazaire", "la roche-sur-yon",
        "cholet", "laval", "saumur", "les sables-d'olonne", "nantais",
        "loire-atlantique", "maine-et-loire", "mayenne",
        "anjou", "vendeen",
    ],
    "Normandie": [
        "rouen", "caen", "le havre", "cherbourg", "evreux", "alencon",
        "saint-lo", "avranches", "lisieux", "coutances", "dieppe", "fecamp",
        "deauville", "trouville", "honfleur", "rouennais",
        "seine-maritime", "calvados",
        "normandie", "normand",
    ],
    "Centre-Val de Loire": [
        "orleans", "bourges", "blois", "chartres", "chateauroux",
        "vendome", "montargis", "joue-les-tours", "vierzon",
        "loiret", "indre-et-loire", "loir-et-cher", "eure-et-loir",
        "berry", "touraine", "sologne", "beauce", "centre-val de loire",
    ],
    "Bourgogne-Franche-Comté": [
        "dijon", "besancon", "belfort", "auxerre", "macon", "chalon-sur-saone",
        "nevers", "vesoul", "lons-le-saunier", "montbeliard",
        "cote-d'or", "doubs", "haute-saone", "saone-et-loire",
        "nievre", "territoire de belfort",
        "bourgogne", "franche-comte", "morvan", "bourguignon",
    ],
    "Corse": [
        "ajaccio", "bastia", "corte", "calvi", "porto-vecchio", "propriano",
        "bonifacio", "ile-rousse",
        "corse-du-sud", "haute-corse",
        "corse",
    ],
    "DOM-TOM": [
        "la reunion", "saint-denis de la reunion", "ile de la reunion",
        "mayotte", "mamoudzou",
        "guadeloupe", "pointe-a-pitre", "basse-terre",
        "martinique", "fort-de-france",
        "guyane francaise", "cayenne", "kourou",
        "saint-barthelemy", "saint-barth",
        "polynesie francaise", "tahiti", "papeete",
        "nouvelle-caledonie", "noumea",
        "wallis-et-futuna",
        "outre-mer", "ultramarin", "antilles francaises",
        "dom-tom",
    ],
}


def detect_region_by_content(title: str, content: str = ""):
    """
    Retourne le nom de la région française détectée dans (title + content),
    ou None si rien ne correspond.

    Compte les matches (word boundary) par région et choisit la mieux scorée.
    En cas d'égalité : priorité à la région avec le plus de hits dans le titre,
    puis à l'ordre du dict.
    """
    title_norm = _norm(title or "")
    text_norm = _norm((title or "") + "\n" + (content or ""))
    if not text_norm:
        return None

    scores = {}
    for region, kws in REGION_KEYWORDS.items():
        cnt = 0
        title_hits = 0
        for kw in kws:
            pat = r'\b' + re.escape(kw) + r'\b'
            cnt += len(re.findall(pat, text_norm))
            title_hits += len(re.findall(pat, title_norm))
        if cnt > 0:
            scores[region] = (cnt, title_hits)
    if not scores:
        return None
    region_order = list(REGION_KEYWORDS.keys())
    best = max(scores.items(), key=lambda kv: (kv[1][0], kv[1][1], -region_order.index(kv[0])))
    return best[0]
