"""Messages renvoyés par l'API, en anglais (défaut) ou en français.

La langue vient du cookie ``fev_lang`` posé par l'interface (bouton EN/FR). Les messages sont
désignés par une clé stable, renvoyée aussi dans le champ ``code`` des erreurs.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

LANGS = ("en", "fr")
DEFAULT_LANG = "en"

MESSAGES: dict[str, dict[str, str]] = {
    # --- authentification
    "auth_required": {"en": "Authentication required.", "fr": "Authentification requise."},
    "account_exists": {"en": "An account already exists.", "fr": "Un compte existe déjà."},
    "password_too_short": {
        "en": "The password must contain at least {n} characters.",
        "fr": "Le mot de passe doit contenir au moins {n} caractères.",
    },
    "too_many_attempts": {"en": "Too many attempts: try again in {s} s.", "fr": "Trop de tentatives : réessayez dans {s} s."},
    "bad_credentials": {"en": "Incorrect username or password.", "fr": "Identifiant ou mot de passe incorrect."},
    "auth_disabled": {"en": "Authentication is disabled (AUTH_DISABLED=true).", "fr": "Authentification désactivée (AUTH_DISABLED=true)."},
    "bad_current_password": {"en": "Current password is incorrect.", "fr": "Mot de passe actuel incorrect."},
    # --- consultation
    "index_loading": {
        "en": "The index is loading, try again in a few seconds.",
        "fr": "Index en cours de chargement, réessayez dans quelques secondes.",
    },
    "bad_date": {
        "en": "Invalid “{name}” parameter: YYYY-MM-DD date expected.",
        "fr": "Paramètre « {name} » invalide : date AAAA-MM-JJ attendue.",
    },
    "path_refused": {
        "en": "Path refused (outside the root, “..” or invalid character).",
        "fr": "Chemin refusé (hors de la racine, « .. » ou caractère interdit).",
    },
    "dir_not_found": {"en": "Folder not found in the index.", "fr": "Dossier introuvable dans l'index."},
    # --- scan
    "scan_started": {"en": "Scan started.", "fr": "Scan lancé."},
    "scan_already_running": {
        "en": "A scan is already running: the request is ignored.",
        "fr": "Un scan est déjà en cours : la demande est ignorée.",
    },
    "index_loading_retry": {
        "en": "The index is still loading: try again in a moment.",
        "fr": "Chargement de l'index en cours : réessayez dans un instant.",
    },
    "root_unavailable": {
        "en": "Unknown root, disabled or missing from /data.",
        "fr": "Racine inconnue, désactivée ou absente de /data.",
    },
    "no_roots": {
        "en": "No enabled root to scan (mount folders under /data).",
        "fr": "Aucune racine active à scanner (montez des dossiers sous /data).",
    },
    "cancel_requested": {"en": "Cancellation requested.", "fr": "Annulation demandée."},
    "no_scan_running": {"en": "No scan is running.", "fr": "Aucun scan en cours."},
    "cancelling": {"en": "Cancelling…", "fr": "Annulation en cours…"},
    "scan_done": {"en": "Scan finished: {n} files indexed.", "fr": "Scan terminé : {n} fichiers indexés."},
    "scan_cancelled": {
        "en": "Scan cancelled: the previous results are kept.",
        "fr": "Scan annulé : les résultats précédents sont conservés.",
    },
    "scan_error": {"en": "Error during the scan: {error}", "fr": "Erreur pendant le scan : {error}"},
    # --- configuration
    "config_invalid": {"en": "Invalid settings.", "fr": "Configuration invalide."},
    "scan_running_cancel_first": {
        "en": "A scan is running: cancel it first.",
        "fr": "Un scan est en cours : annulez-le d'abord.",
    },
    "cache_cleared": {"en": "Cache cleared. {detail}", "fr": "Cache vidé. {detail}"},
    # --- validation de config.yaml (préfixées « v_ »)
    "v_folder": {"en": "must be a direct folder name under /data", "fr": "doit être un nom de dossier direct de /data"},
    "v_hhmm": {"en": "HH:MM format expected (e.g. 01:30)", "fr": "format HH:MM attendu (ex. 01:30)"},
    "v_pattern": {"en": "invalid pattern", "fr": "motif invalide"},
    "v_unknown_category": {
        "en": "unknown category “{cid}” (expected: {expected})",
        "fr": "catégorie inconnue « {cid} » (attendu : {expected})",
    },
    "v_bad_extension": {"en": "invalid extension “{ext}” in {cid}", "fr": "extension invalide « {ext} » dans {cid}"},
    "v_ext_conflict": {
        "en": "extension “{ext}” is in both {a} and {b}",
        "fr": "l'extension « {ext} » est à la fois dans {a} et {b}",
    },
    "v_duplicate_roots": {"en": "duplicate root identifiers", "fr": "identifiants de racines en double"},
    "v_ge": {"en": "must be ≥ {n}", "fr": "doit être ≥ {n}"},
    "v_le": {"en": "must be ≤ {n}", "fr": "doit être ≤ {n}"},
    "v_extra": {"en": "unknown key", "fr": "clé inconnue"},
    "v_missing": {"en": "required value", "fr": "valeur obligatoire"},
    "v_int": {"en": "integer expected", "fr": "nombre entier attendu"},
    "v_bool": {"en": "boolean expected (true/false)", "fr": "booléen attendu (true/false)"},
    "v_id_chars": {"en": "allowed characters: letters, digits, . _ -", "fr": "caractères autorisés : lettres, chiffres, . _ -"},
    # --- export CSV
    "csv_header": {
        "en": "root;path;name;extension;category;size_bytes;modified;created",
        "fr": "racine;chemin;nom;extension;catégorie;taille_octets;modification;création",
    },
}


def lang_of(request: Request | None) -> str:
    """Langue de l'utilisateur : cookie ``fev_lang`` posé par l'interface, anglais sinon."""
    if request is None:
        return DEFAULT_LANG
    value = request.cookies.get("fev_lang", "")
    return value if value in LANGS else DEFAULT_LANG


def tr(key: str, lang: str = DEFAULT_LANG, **params: Any) -> str:
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry[DEFAULT_LANG]
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text


class ApiError(HTTPException):
    """Erreur HTTP dont le message est traduit au moment de répondre (voir main.py)."""

    def __init__(self, status_code: int, key: str, headers: dict[str, str] | None = None, **params: Any):
        super().__init__(status_code, tr(key, DEFAULT_LANG, **params), headers)
        self.key = key
        self.params = params
