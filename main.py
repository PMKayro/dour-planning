"""Dour Festival 2026 -> Airtable.

Scrape les pages "jour" du site Dour (15 au 19 juillet 2026), recupere pour
chaque artiste : nom, jour, lien vers sa page Dour, lien Spotify, et (quand le
timetable sera publie) la scene + l'horaire. Pousse tout dans une table Airtable.

Re-runnable : l'upsert se fait sur (Page Dour, Jour), donc relancer le script
met a jour les infos SANS jamais ecraser les colonnes de vote de l'equipe.

Usage (sur T-600) :
    AIRTABLE_TOKEN=pat...  AIRTABLE_BASE_ID=app...  python3 main.py
"""

import logging
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from pyairtable import Api

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BASE_URL = "https://www.dourfestival.eu"

# slug d'URL -> libelle affiche dans Airtable
DAYS = {
    "15-juillet": "15 juillet",
    "16-juillet": "16 juillet",
    "17-juillet": "17 juillet",
    "18-juillet": "18 juillet",
    "19-juillet": "19 juillet",
}

# Une colonne checkbox "je veux voir" par membre de l'equipe
TEAM = ["Max", "Kev", "MyMy", "Levi", "PM", "Alex", "Neavus"]

# Scenes connues, detectees dans le texte de la page artiste.
# Ordre = longueur decroissante pour matcher l'alias le plus precis d'abord.
# (Dour annonce 11 scenes ; on complete cette liste apres le 1er run grace aux
#  warnings "scene non detectee" dans les logs.)
STAGE_ALIASES = {
    "La Petite Maison dans la Prairie": "La Petite Maison dans la Prairie",
    "The Last Arena": "The Last Arena",
    "De Balzaal": "De Balzaal",
    "Dub Corner": "Dub Corner",
    "L'Atelier": "L'Atelier",
    "Boombox": "Boombox",
    "La PMP": "La Petite Maison dans la Prairie",
    "PMP": "La Petite Maison dans la Prairie",
}
STAGES = sorted(STAGE_ALIASES, key=len, reverse=True)

TABLE_NAME = "Line-up"
REQUEST_DELAY = 0.4  # politesse entre deux requetes
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; dour-planning/1.0)"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("dour")


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #
def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def slug_to_name(href: str) -> str:
    """/artiste/231-damso/ -> 'Damso' (fallback si pas de texte de lien)."""
    m = re.search(r"/artiste/\d+-(.+?)/?$", href)
    if not m:
        return href
    return m.group(1).replace("-", " ").title()


def _norm(s: str) -> str:
    """Minuscule + apostrophes typographiques -> droites (pour matcher L'Atelier)."""
    return s.lower().replace("’", "'").replace("‘", "'").replace("ʼ", "'")


def detect_stage(text: str) -> str:
    """Repere une scene connue dans le texte de la page artiste."""
    low = _norm(text)
    for alias in STAGES:
        if _norm(alias) in low:
            return STAGE_ALIASES[alias]
    return ""


def normalize_spotify(src: str) -> str:
    """Transforme une URL d'embed Spotify en lien public open.spotify.com."""
    src = src.split("?")[0]
    src = src.replace("/embed/", "/")
    if src.startswith("//"):
        src = "https:" + src
    return src


def scrape_day(slug: str, label: str) -> dict[str, dict]:
    """Retourne {url_page_artiste: {name, url, jour}} pour un jour donne."""
    url = f"{BASE_URL}/{slug}/"
    log.info("Jour %s  ->  %s", label, url)
    soup = get_soup(url)

    artists: dict[str, dict] = {}
    for a in soup.select('a[href*="/artiste/"]'):
        href = a.get("href") or ""
        if not href:
            continue
        full = href if href.startswith("http") else BASE_URL + href

        name = a.get_text(" ", strip=True)
        if not name:
            img = a.find("img")
            name = (img.get("alt").strip() if img and img.get("alt") else "") or slug_to_name(href)

        artists.setdefault(full, {"name": name, "url": full, "jour": label})

    log.info("  -> %d artistes", len(artists))
    return artists


def enrich(artist: dict) -> dict:
    """Visite la page artiste pour recuperer le lien Spotify (+ nom propre)."""
    try:
        soup = get_soup(artist["url"])
    except Exception as exc:  # noqa: BLE001
        log.warning("  page KO %s (%s)", artist["url"], exc)
        return artist

    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        artist["name"] = h1.get_text(strip=True)

    spotify = ""
    for tag in soup.find_all(["iframe", "a"]):
        src = tag.get("src") or tag.get("href") or ""
        if "spotify.com" in src:
            spotify = normalize_spotify(src)
            break
    artist["spotify"] = spotify

    artist["scene"] = detect_stage(soup.get_text(" ", strip=True))
    if not artist["scene"]:
        log.warning("  scene non detectee pour %s", artist["name"])
    return artist


def scrape_all() -> list[dict]:
    found: dict[str, dict] = {}
    for slug, label in DAYS.items():
        try:
            found.update(scrape_day(slug, label))
        except Exception as exc:  # noqa: BLE001
            log.warning("Jour %s ignore (%s)", label, exc)
        time.sleep(REQUEST_DELAY)

    log.info("Enrichissement Spotify de %d artistes...", len(found))
    artists = []
    for art in found.values():
        artists.append(enrich(art))
        time.sleep(REQUEST_DELAY)
    return artists


# --------------------------------------------------------------------------- #
# Airtable
# --------------------------------------------------------------------------- #
def build_fields_schema() -> list[dict]:
    """Schema de la table. Le 1er champ devient le champ primaire."""
    fields = [
        {"name": "Artiste", "type": "singleLineText"},
        {
            "name": "Jour",
            "type": "singleSelect",
            "options": {"choices": [{"name": v} for v in DAYS.values()]},
        },
        {"name": "Scene", "type": "singleLineText"},
        {"name": "Heure", "type": "singleLineText"},
        {"name": "Genre", "type": "singleLineText"},
        {"name": "Page Dour", "type": "url"},
        {"name": "Spotify", "type": "url"},
    ]
    for person in TEAM:
        fields.append(
            {
                "name": person,
                "type": "checkbox",
                "options": {"icon": "heart", "color": "redBright"},
            }
        )
    return fields


def ensure_table(base):
    existing = {t.name for t in base.schema().tables}
    if TABLE_NAME in existing:
        log.info("Table '%s' deja presente.", TABLE_NAME)
        return base.table(TABLE_NAME)
    log.info("Creation de la table '%s'...", TABLE_NAME)
    base.create_table(TABLE_NAME, build_fields_schema())
    return base.table(TABLE_NAME)


def sync(artists: list[dict]):
    api = Api(os.environ["AIRTABLE_TOKEN"])
    base = api.base(os.environ["AIRTABLE_BASE_ID"])
    table = ensure_table(base)

    records = []
    for art in artists:
        fields = {
            "Artiste": art["name"],
            "Jour": art["jour"],
            "Page Dour": art["url"],
        }
        if art.get("spotify"):
            fields["Spotify"] = art["spotify"]
        if art.get("scene"):
            fields["Scene"] = art["scene"]
        records.append({"fields": fields})

    # Upsert sur (Page Dour, Jour) : ne touche pas aux colonnes de vote.
    res = table.batch_upsert(records, key_fields=["Page Dour", "Jour"], typecast=True)
    created = len(res.get("createdRecords", []))
    log.info("Airtable sync OK : %d records (%d crees).", len(records), created)


# --------------------------------------------------------------------------- #
def main():
    for var in ("AIRTABLE_TOKEN", "AIRTABLE_BASE_ID"):
        if not os.environ.get(var):
            sys.exit(f"Variable d'environnement manquante : {var}")

    artists = scrape_all()
    if not artists:
        sys.exit("Aucun artiste trouve - le HTML du site a peut-etre change.")
    sync(artists)


if __name__ == "__main__":
    main()
