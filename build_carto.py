#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_carto.py — Régénère les données de la cartographie Jeunes360 depuis Notion.

Lit la base Notion "Insertion des jeunes - Mapping existant", reconstruit
arbre_opportunites_data.json, et réinjecte les données dans arbre_opportunites.html
(qui embarque le tableau `const SERVICES = [...]` en dur).

Usage :
    export NOTION_TOKEN="ntn_xxx"          # jeton d'intégration interne Notion
    python build_carto.py                   # build complet (JSON + HTML)
    python build_carto.py --check           # n'écrit rien, signale juste les écarts

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
DATA_SOURCE_ID = "31708bca-6029-8049-81d6-000bb4b8a8c5"  # collection (data source)
# La base n'a qu'une data source ; l'API "query database" accepte le database_id.
DATABASE_ID = "31708bca-6029-8065-ac96-c341eb181d65"

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(HERE, "arbre_opportunites_data.json")
HTML_PATH = os.path.join(HERE, "arbre_opportunites.html")

# --- Mapping des familles de publics (pour info / cohérence éventuelle) -------
# Le HTML gère ses propres familles ; le script ne touche QUE le tableau SERVICES.

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
            if e.code == 429:  # rate limit
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
    """Récupère toutes les pages de la base, en paginant."""
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
    """Concatène un tableau rich_text/title en texte simple."""
    return "".join(seg.get("plain_text", "") for seg in (rich or [])).strip()


def _multi(prop):
    return [o["name"] for o in prop.get("multi_select", [])] if prop else []


def _select(prop):
    sel = prop.get("select") if prop else None
    return sel["name"] if sel else ""


def page_to_service(page):
    """Transforme une page Notion en dict au format attendu par le HTML/JSON."""
    props = page.get("properties", {})

    def P(name):
        return props.get(name, {})

    nom = _plain(P("Nom du service").get("title"))
    porteurs = _multi(P("Porté par"))

    # URL : propriété renommée "URL" dans Notion (clé interne userDefined:URL)
    url_prop = P("URL")
    url = url_prop.get("url") or "" if url_prop else ""

    beta_sel = _select(P("Beta.gouv.fr"))

    # Lien Notion = URL canonique de la page (vrai ID, corrige les liens)
    notion_id = page["id"].replace("-", "")
    notion_url = "https://www.notion.so/" + notion_id

    return {
        "nom": nom,
        "publics": _multi(P("Public Cible")),
        "categories": _multi(P("Catégories")),
        "use_cases": _multi(P("Cas d'usages")),
        "etapes": _multi(P("Étape du parcours d'un jeune")),
        "objectif": _plain(P("Objectif de la plateforme").get("rich_text")),
        "porteur": ", ".join(porteurs),
        "url": url,
        "statut": _select(P("Statut")),
        "marqueur": _multi(P("Marqueur")),
        "volume": _select(P("Volume d'usage")),
        "beta": beta_sel if beta_sel else "❌",
        "justification": _plain(P("Justification courte").get("rich_text")),
        "notion": notion_url,
        "quand_mobiliser": _plain(P("Quand mobiliser").get("rich_text")),
    }


# --- Construction et écriture ------------------------------------------------

def build_services():
    pages = query_all_pages()
    services = [page_to_service(p) for p in pages]
    # On ne garde que les fiches qui ont un nom et un statut renseigné OU
    # explicitement hors-arbitrage (sinon on inclut des pages parasites).
    services = [s for s in services if s["nom"]]
    # Tri stable par nom pour limiter le bruit de diff entre deux builds.
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
    """Compare aux fichiers existants sans rien écrire."""
    print(f"  {len(services)} services lus depuis Notion.")
    sans_statut = [s["nom"] for s in services if not s["statut"]]
    if sans_statut:
        print(f"  ATTENTION : {len(sans_statut)} sans statut : {sans_statut}")
    sans_qm = [s["nom"] for s in services if not s["quand_mobiliser"]]
    if sans_qm:
        print(f"  ATTENTION : {len(sans_qm)} sans 'quand mobiliser' : {sans_qm}")
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
    print("Lecture de la base Notion...")
    services = build_services()
    if check:
        check_only(services)
        return
    write_json(services)
    inject_into_html(services)
    print("Terminé.")


if __name__ == "__main__":
    main()
