#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_bubble.py — Régénère la bubble chart « par segment fonctionnel » depuis Notion.

Lit la base Notion « Insertion des jeunes - Mapping existant », reconstruit
bubble_chart_data.json, et réinjecte le tableau `const SERVICES = [...]` dans
bubble_chart.html (qui l'embarque en dur, pour fonctionner sans serveur).

Usage :
    export NOTION_TOKEN="ntn_xxx"          # jeton d'intégration interne Notion
    python build_bubble.py                 # build complet (JSON + HTML)
    python build_bubble.py --check         # n'écrit rien, signale juste les écarts

Le script n'écrit JAMAIS dans Notion : il lit seulement. La source de vérité
est Notion ; ce script en dérive les fichiers statiques du dépôt.
"""

import os
import sys
import json
import re
import time
import urllib.request
import urllib.error

# --- Configuration -----------------------------------------------------------

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
NOTION_VERSION = "2022-06-28"
DATABASE_ID = "31708bca-6029-8065-ac96-c341eb181d65"   # base (une seule data source)

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(HERE, "bubble_chart_data.json")
HTML_PATH = os.path.join(HERE, "bubble_chart.html")

# --- Helpers API Notion ------------------------------------------------------

def _req(url, method="GET", payload=None):
    if not NOTION_TOKEN:
        sys.exit("ERREUR : variable d'environnement NOTION_TOKEN absente.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + NOTION_TOKEN)
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429:
                wait = 2 ** attempt
                print(f"  rate limit, attente {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"ERREUR API Notion {e.code} sur {url}\n{body}")
        except urllib.error.URLError as e:
            wait = 2 ** attempt
            print(f"  erreur réseau ({e}), retry dans {wait}s...", file=sys.stderr)
            time.sleep(wait)
    sys.exit("ERREUR : échec répété de l'appel API Notion.")


def query_all_pages():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    pages, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        res = _req(url, method="POST", payload=payload)
        pages.extend(res.get("results", []))
        if res.get("has_more"):
            cursor = res.get("next_cursor")
        else:
            break
    return pages


# --- Extraction des propriétés ----------------------------------------------

def _plain(rich):
    return "".join(seg.get("plain_text", "") for seg in (rich or [])).strip()

def _multi(prop):
    return [o["name"] for o in prop.get("multi_select", [])] if prop else []

def _select(prop):
    sel = prop.get("select") if prop else None
    return sel["name"] if sel else ""

# regroupement des publics cibles (14 valeurs Notion) en familles de colonnes
PUBLIC_FAMILY = {
    "Collégiens": "Scolaires",
    "Lycéens": "Scolaires",
    "Lycéens voie pro / apprentis": "Scolaires",
    "Étudiants": "Étudiants",
    "Jeunes de 15 à 30 ans": "Jeunes généralistes",
    "Jeunes diplômés primo-entrant": "Jeunes généralistes",
    "Jeunes en insertion (NEET décrocheurs)": "NEET & jeunes fragiles",
    "Accompagnants": "Pros & structures",
    "Équipes pédagogiques": "Pros & structures",
    "Gestionnaires": "Pros & structures",
    "Chercheurs": "Pros & structures",
    "Organismes de formation / CFA": "Pros & structures",
    "Employeurs / Entreprises": "Pros & structures",
    "Organisation engagement (associatives publiques et privées)": "Pros & structures",
}

def _public_families(prop):
    """Multi-select Public Cible -> familles dédupliquées, dans l'ordre canonique."""
    raw = _multi(prop)
    order = ["Scolaires", "Étudiants", "Jeunes généralistes",
             "NEET & jeunes fragiles", "Pros & structures"]
    fams = {PUBLIC_FAMILY[v] for v in raw if v in PUBLIC_FAMILY}
    return [f for f in order if f in fams]


def _volume(prop):
    """ '1 - Niche' -> 1 ... '0 - Inconnu' / vide -> None. """
    raw = _select(prop)
    if not raw:
        return None
    m = re.match(r"\s*(\d+)", raw)
    if not m:
        return None
    n = int(m.group(1))
    return n if n > 0 else None


def page_to_service(page):
    props = page.get("properties", {})

    def P(name):
        return props.get(name, {})

    nom = _plain(P("Nom du service").get("title"))

    url_prop = P("URL")
    url = (url_prop.get("url") or "") if url_prop else ""

    notion_id = page["id"].replace("-", "")
    notion_url = "https://www.notion.so/" + notion_id

    return {
        "nom": nom,
        "segments": _multi(P("Segment fonctionnel")),
        "publics": _public_families(P("Public Cible")),
        "volume": _volume(P("Volume d'usage")),
        "arbitrage": _select(P("Arbitrage")),
        "statut": _select(P("Statut")),
        "justification": _plain(P("Justification courte").get("rich_text")),
        "objectif": _plain(P("Objectif de la plateforme").get("rich_text")),
        "marqueur": _multi(P("Marqueur")),
        "doublons": _multi(P("Doublon")),
        "url": url,
        "notion": notion_url,
        "maturite": _select(P("Maturité de la plateforme")),
    }


# --- Construction et écriture ------------------------------------------------

# ordre canonique des segments (doit rester aligné avec SEGMENTS dans le HTML)
SEGMENT_ORDER = [
    "Information et orientation grand public",
    "Formation initiale et orientation scolaire",
    "Recherche de formation professionnelle",
    "Alternance",
    "Recherche d'emploi",
    "Accompagnement vers l'emploi",
    "Aides et droits sociaux",
    "Engagement et service civique",
    "Communs numériques",
]

def build_services():
    pages = query_all_pages()
    services = [page_to_service(p) for p in pages]
    services = [s for s in services if s["nom"]]
    services.sort(key=lambda s: s["nom"].lower())
    return services


def write_json(services):
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(services, f, ensure_ascii=False, indent=2)
    print(f"  → {os.path.basename(JSON_PATH)} écrit ({len(services)} services)")


def inject_into_html(services):
    if not os.path.exists(HTML_PATH):
        print(f"  ! {os.path.basename(HTML_PATH)} introuvable, HTML non régénéré.")
        return
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    js_array = json.dumps(services, ensure_ascii=False)
    new_line = "const SERVICES = " + js_array + ";"
    html2, n = re.subn(r"const SERVICES = \[.*?\];", lambda m: new_line,
                       html, count=1, flags=re.DOTALL)
    if n != 1:
        sys.exit("ERREUR : bloc `const SERVICES = [...]` introuvable dans le HTML.")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html2)
    print(f"  → {os.path.basename(HTML_PATH)} mis à jour (tableau SERVICES réinjecté)")


def check_only(services):
    print(f"  {len(services)} services lus depuis Notion.")
    connus = set(SEGMENT_ORDER)
    for s in services:
        inconnus = [seg for seg in s["segments"] if seg not in connus]
        if inconnus:
            print(f"  ATTENTION : segment hors référentiel sur {s['nom']!r} : {inconnus}")
    sans_seg = [s["nom"] for s in services if not s["segments"]]
    if sans_seg:
        print(f"  {len(sans_seg)} service(s) sans segment fonctionnel : {sans_seg}")
    sans_arb = [s["nom"] for s in services if not s["arbitrage"]]
    if sans_arb:
        print(f"  {len(sans_arb)} service(s) sans arbitrage : {sans_arb}")
    if os.path.exists(JSON_PATH):
        old = {s["nom"]: s for s in json.load(open(JSON_PATH, encoding="utf-8"))}
        new = {s["nom"]: s for s in services}
        ajoutes = set(new) - set(old)
        retires = set(old) - set(new)
        if ajoutes:
            print(f"  Nouveaux services : {sorted(ajoutes)}")
        if retires:
            print(f"  Services disparus : {sorted(retires)}")
        diffs = 0
        for nom in set(old) & set(new):
            for k in new[nom]:
                if old[nom].get(k) != new[nom].get(k):
                    diffs += 1
                    print(f"  ~ {nom} / {k} change")
        print(f"  {diffs} champ(s) modifié(s) au total.")


def main():
    check = "--check" in sys.argv
    print("Lecture de la base Notion (bubble chart par segment)...")
    services = build_services()
    if check:
        check_only(services)
        return
    write_json(services)
    inject_into_html(services)
    print("Terminé.")


if __name__ == "__main__":
    main()
