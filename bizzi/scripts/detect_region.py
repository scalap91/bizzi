import os
"""
Détecte la région d'un article Onyx à partir de villes/départements/régions
mentionnés dans le titre + contenu, et UPDATE productions.region_id.

Articles internationaux ou nationaux sans mention géographique → restent SANS région.

Usage :
    python3 detect_region.py            # rétro-applique sur tous les articles sans région
    python3 detect_region.py --dry-run  # affiche les matches sans écrire
    python3 detect_region.py --limit 50 # par lots
"""
import argparse
import re
import sys
import unicodedata

sys.path.insert(0, '/opt/bizzi/bizzi')

from sqlalchemy import create_engine, text

DB = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))


def _norm(s: str) -> str:
    """Normalise (sans accents, minuscules) pour matching robuste."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


# Mapping région → mots-clés. Toutes valeurs en minuscule + sans accents.
# Mots ambigus retirés : nord/sud/centre, fleuves (loire, rhone, meuse), départements
# courts (var, ain, cher, indre, charente, sarthe, vendee, lot, gers, tarn, ariege, gard,
# essonne, marne, aube, oise, aisne, somme, manche, eure, orne, jura, yonne),
# vienne (Autriche), champagne (vin), monaco/saint-pierre/saint-martin (étrangers/ambigus),
# mont-saint-michel (touristique national).
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


def detect_region(text_norm: str, title_norm: str):
    """Compte les matches par région et retourne (region_name, score) du gagnant, ou (None, 0)."""
    scores = {}
    for region, kws in REGION_KEYWORDS.items():
        cnt = 0
        title_hits = 0
        for kw in kws:
            # Word boundary pour éviter matchs partiels (ex "ain" dans "main")
            pattern = r'\b' + re.escape(kw) + r'\b'
            cnt += len(re.findall(pattern, text_norm))
            title_hits += len(re.findall(pattern, title_norm))
        if cnt > 0:
            scores[region] = (cnt, title_hits)
    if not scores:
        return None, 0, 0
    # Trier : (matches total desc, matches titre desc, ordre du dict comme tiebreak)
    region_order = list(REGION_KEYWORDS.keys())
    best = max(scores.items(), key=lambda kv: (kv[1][0], kv[1][1], -region_order.index(kv[0])))
    return best[0], best[1][0], best[1][1]


def main(limit, dry_run):
    # Charger le mapping name → id depuis la DB
    with DB.connect() as conn:
        regions = {r[1]: r[0] for r in conn.execute(
            text("SELECT id, name FROM regions WHERE tenant_id=1")
        ).fetchall()}
    print(f"Régions DB : {len(regions)}")

    # Articles sans région
    with DB.connect() as conn:
        sql = """
            SELECT id, title, content_raw
            FROM productions
            WHERE tenant_id=1 AND status='published' AND region_id IS NULL
              AND content_raw IS NOT NULL
            ORDER BY created_at DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(text(sql)).fetchall()

    print(f"Articles sans région à analyser : {len(rows)}\n")

    by_region = {}
    none_count = 0
    updated = 0
    for r in rows:
        aid, title, content_raw = r
        title_n = _norm(title or "")
        text_n = _norm((title or "") + "\n" + (content_raw or ""))
        region, score, title_hits = detect_region(text_n, title_n)
        if not region:
            none_count += 1
            continue
        by_region[region] = by_region.get(region, 0) + 1
        if not dry_run and region in regions:
            with DB.connect() as conn:
                conn.execute(
                    text("UPDATE productions SET region_id=:rid, updated_at=now() WHERE id=:aid AND tenant_id=1 AND region_id IS NULL"),
                    {"rid": regions[region], "aid": aid}
                )
                conn.commit()
                updated += 1

    print("=== Bilan ===")
    print(f"  Total analysés : {len(rows)}")
    print(f"  Détectés       : {len(rows) - none_count}")
    print(f"  Sans match     : {none_count} (international/national flou)")
    print(f"  Mis à jour DB  : {updated} {'(DRY-RUN)' if dry_run else ''}\n")
    print("=== Distribution détectée ===")
    for region, n in sorted(by_region.items(), key=lambda x: -x[1]):
        print(f"  {region:25s} {n}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="0 = tous")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(args.limit, args.dry_run)
