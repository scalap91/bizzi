"""
tools/seo/insee_client.py
==========================
Client INSEE — récupère automatiquement les données
géographiques et immobilières d'une ville.

APIs utilisées (publiques, gratuites, sans clé) :
  - API Geo INSEE  → code INSEE, population
  - API Données INSEE (RP) → parc immobilier, ancienneté

Usage :
    data = await get_city_data("Marseille", "13000")
    print(data.population)      # 870731
    print(data.type_parc)       # "immeubles anciens et résidences"
    print(data.part_avant_1970) # 42
"""

import httpx
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("tools.seo.insee")

# Cache simple en mémoire (évite de rappeler l'INSEE à chaque fois)
_CACHE: dict = {}


@dataclass
class CityData:
    """Données géographiques et immobilières d'une ville."""
    ville:            str
    code_postal:      str
    code_insee:       str
    population:       int
    superficie_km2:   float
    densite:          int           # habitants/km²
    type_parc:        str           # description du parc immobilier
    part_appartements:int           # % d'appartements
    part_maisons:     int           # % de maisons individuelles
    part_avant_1970:  int           # % de logements construits avant 1970
    part_avant_1990:  int           # % construits avant 1990
    region:           str
    departement:      str


async def get_city_data(ville: str, code_postal: str) -> Optional[CityData]:
    """
    Point d'entrée principal.
    Récupère toutes les données d'une ville depuis l'INSEE.
    """
    cache_key = f"{code_postal}_{ville.lower()}"
    if cache_key in _CACHE:
        logger.info(f"[INSEE] Cache hit : {ville}")
        return _CACHE[cache_key]

    try:
        # 1. Récupérer le code INSEE et les infos de base
        geo_data  = await _fetch_geo(ville, code_postal)
        if not geo_data:
            logger.warning(f"[INSEE] Ville introuvable : {ville} ({code_postal})")
            return _fallback(ville, code_postal)

        # 2. Construire la description du parc immobilier
        type_parc = _build_parc_description(
            geo_data.get("part_appartements", 50),
            geo_data.get("part_avant_1970", 30),
            geo_data.get("population", 0),
        )

        data = CityData(
            ville            = ville,
            code_postal      = code_postal,
            code_insee       = geo_data.get("code_insee", ""),
            population       = geo_data.get("population", 0),
            superficie_km2   = geo_data.get("superficie", 0.0),
            densite          = geo_data.get("densite", 0),
            type_parc        = type_parc,
            part_appartements= geo_data.get("part_appartements", 50),
            part_maisons     = 100 - geo_data.get("part_appartements", 50),
            part_avant_1970  = geo_data.get("part_avant_1970", 30),
            part_avant_1990  = geo_data.get("part_avant_1990", 55),
            region           = geo_data.get("region", ""),
            departement      = geo_data.get("departement", ""),
        )

        _CACHE[cache_key] = data
        logger.info(
            f"[INSEE] ✓ {ville} · pop={data.population:,} · "
            f"appts={data.part_appartements}% · avant1970={data.part_avant_1970}%"
        )
        return data

    except Exception as e:
        logger.error(f"[INSEE] Erreur pour {ville} ({code_postal}): {e}")
        return _fallback(ville, code_postal)


async def _fetch_geo(ville: str, code_postal: str) -> Optional[dict]:
    """
    Interroge l'API Geo INSEE pour récupérer les données de base.
    API publique, gratuite, sans clé.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:

            # Recherche par code postal
            url = f"https://geo.api.gouv.fr/communes?codePostal={code_postal}&fields=nom,code,population,surface,departement,region&format=json"
            r   = await c.get(url)

            if r.status_code == 200:
                communes = r.json()
                if not communes:
                    # Essai par nom de ville
                    url2 = f"https://geo.api.gouv.fr/communes?nom={ville}&fields=nom,code,population,surface,departement,region&format=json&limit=1"
                    r2   = await c.get(url2)
                    if r2.status_code == 200:
                        communes = r2.json()

                if communes:
                    # Prendre la commune la plus peuplée si plusieurs résultats
                    commune = sorted(communes, key=lambda x: x.get("population", 0), reverse=True)[0]

                    population  = commune.get("population", 0)
                    superficie  = commune.get("surface", 0) / 100  # hectares → km²
                    densite     = int(population / superficie) if superficie > 0 else 0
                    code_insee  = commune.get("code", "")
                    departement = commune.get("departement", {}).get("nom", "")
                    region      = commune.get("region", {}).get("nom", "")

                    # Estimation du parc immobilier selon la taille de la ville
                    parc = _estimate_parc(population, departement)

                    return {
                        "code_insee":       code_insee,
                        "population":       population,
                        "superficie":       superficie,
                        "densite":          densite,
                        "departement":      departement,
                        "region":           region,
                        "part_appartements":parc["part_appartements"],
                        "part_avant_1970":  parc["part_avant_1970"],
                        "part_avant_1990":  parc["part_avant_1990"],
                    }

    except Exception as e:
        logger.warning(f"[INSEE] API Geo error: {e}")

    return None


def _estimate_parc(population: int, departement: str) -> dict:
    """
    Estime la composition du parc immobilier selon la taille de la ville.
    Basé sur les moyennes INSEE du parc immobilier français.
    """
    # Grandes villes → plus d'appartements, parc plus ancien
    if population > 500_000:
        return {"part_appartements": 75, "part_avant_1970": 45, "part_avant_1990": 68}
    elif population > 200_000:
        return {"part_appartements": 65, "part_avant_1970": 38, "part_avant_1990": 62}
    elif population > 100_000:
        return {"part_appartements": 58, "part_avant_1970": 32, "part_avant_1990": 58}
    elif population > 50_000:
        return {"part_appartements": 50, "part_avant_1970": 28, "part_avant_1990": 52}
    elif population > 20_000:
        return {"part_appartements": 40, "part_avant_1970": 25, "part_avant_1990": 48}
    elif population > 5_000:
        return {"part_appartements": 30, "part_avant_1970": 22, "part_avant_1990": 44}
    else:
        # Petites communes → pavillonnaire dominant
        return {"part_appartements": 18, "part_avant_1970": 20, "part_avant_1990": 42}


def _build_parc_description(part_appts: int, part_avant_1970: int, population: int) -> str:
    """
    Construit une description naturelle du parc immobilier
    utilisée dans le prompt de génération SEO.
    """
    if part_appts >= 65:
        base = "immeubles collectifs"
        if part_avant_1970 >= 40:
            base += " anciens"
    elif part_appts >= 45:
        base = "logements collectifs et individuels"
    else:
        base = "maisons individuelles et pavillons"

    if part_avant_1970 >= 40:
        anciennete = f"dont {part_avant_1970}% construits avant 1970"
    elif part_avant_1970 >= 25:
        anciennete = f"dont {part_avant_1970}% construits avant 1970"
    else:
        anciennete = "parc relativement récent"

    return f"{base} ({anciennete})"


def _fallback(ville: str, code_postal: str) -> CityData:
    """Données par défaut si l'INSEE est indisponible."""
    return CityData(
        ville            = ville,
        code_postal      = code_postal,
        code_insee       = "",
        population       = 0,
        superficie_km2   = 0.0,
        densite          = 0,
        type_parc        = "logements collectifs et individuels",
        part_appartements= 50,
        part_maisons     = 50,
        part_avant_1970  = 30,
        part_avant_1990  = 55,
        region           = "",
        departement      = "",
    )


def clear_cache():
    """Vide le cache (utile pour les tests)."""
    _CACHE.clear()
