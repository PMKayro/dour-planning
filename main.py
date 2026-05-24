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


def absolutize(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    return url


def img_url(img) -> str:
    """Meilleure URL d'image dispo dans une balise <img> (gere le lazy-load)."""
    if not img:
        return ""
    src = img.get("data-src") or img.get("src") or ""
    if (not src or "data:image" in src) and img.get("srcset"):
        src = img["srcset"].split(",")[0].strip().split(" ")[0]
    return absolutize(src)


def card_image(link) -> str:
    """Image de l'artiste : dans la carte 'flip-box' qui entoure le lien popup."""
    card = link.find_parent("div", class_="flip-box-inner") or link.find_parent("div", class_="flip-box")
    if not card:
        return ""
    for img in card.find_all("img"):
        url = img_url(img)
        if "azureedge" in url:  # CDN des photos d'artistes (ignore logos/pixels)
            return url
    return ""


def scrape_day(slug: str, label: str) -> dict[str, dict]:
    """Retourne {url_page_artiste: {name, url, jour}} pour un jour donne."""
    url = f"{BASE_URL}/{slug}/"
    log.info("Jour %s  ->  %s", label, url)
    soup = get_soup(url)

    artists: dict[str, dict] = {}
    for a in soup.select("a.artist-popup-link"):
        href = a.get("href") or ""
        if "/artiste/" not in href:
            continue
        full = href if href.startswith("http") else BASE_URL + href
        # le texte du lien = "En savoir plus" : le vrai nom vient du h1 (enrich)
        artists.setdefault(
            full,
            {"name": slug_to_name(href), "url": full, "jour": label, "image": card_image(a)},
        )

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

    # Image de la carte = photo de l'artiste (CDN). og:image seulement en secours.
    if not artist.get("image"):
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            artist["image"] = absolutize(og["content"].strip())
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
        {"name": "Image", "type": "multipleAttachments"},
        {"name": "Image URL", "type": "url"},
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
    schema = build_fields_schema()
    tables = {t.name for t in base.schema().tables}
    if TABLE_NAME not in tables:
        log.info("Creation de la table '%s'...", TABLE_NAME)
        base.create_table(TABLE_NAME, schema)
        return base.table(TABLE_NAME)

    log.info("Table '%s' deja presente.", TABLE_NAME)
    table = base.table(TABLE_NAME)
    existing_fields = {f.name for f in table.schema().fields}
    for field in schema:
        if field["name"] not in existing_fields:
            log.info("Ajout du champ manquant '%s'", field["name"])
            table.create_field(field["name"], field["type"], options=field.get("options"))
    return table


def sync(artists: list[dict]):
    api = Api(os.environ["AIRTABLE_TOKEN"])
    base = api.base(os.environ["AIRTABLE_BASE_ID"])
    table = ensure_table(base)

    # Artistes ayant deja une image -> on ne la recharge pas (sinon doublons a chaque run)
    has_image = set()
    for rec in table.all(fields=["Page Dour", "Jour", "Image"]):
        f = rec["fields"]
        if f.get("Image"):
            has_image.add((f.get("Page Dour"), f.get("Jour")))

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
        if art.get("image"):
            fields["Image URL"] = art["image"]
            if (art["url"], art["jour"]) not in has_image:
                fields["Image"] = [{"url": art["image"]}]
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
