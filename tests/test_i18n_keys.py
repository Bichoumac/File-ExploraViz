"""Cohérence des traductions de l'interface : chaque clé utilisée existe en anglais et en français."""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "web" / "assets"

# Familles de clés construites dynamiquement (t(`cat.${id}`), etc.) et leurs suffixes attendus.
DYNAMIC = {
    "cat.": ["photo", "video", "audio", "document", "spreadsheet", "presentation", "pdf", "archive", "code", "executable", "other"],
    "unit.": ["B", "K", "M", "G", "T", "P"],
    "ex.sort.": ["path", "name", "-name", "-size", "size", "-mtime", "mtime", "ext", "category"],
    "ex.preset.": ["all", "7d", "30d", "year", "gt1y", "gt5y", "custom"],
    "scan.status.": ["idle", "running", "cancelling", "done", "cancelled", "error"],
    "st.age.": [str(i) for i in range(7)],
    "usr.role.": ["admin", "viewer"],
}


def dictionaries() -> dict[str, set[str]]:
    src = (ASSETS / "i18n.js").read_text(encoding="utf-8")
    blocks = re.split(r"^\s{2}(en|fr): \{$", src, flags=re.M)
    out = {}
    for name, body in zip(blocks[1::2], blocks[2::2], strict=True):
        body = body.split("\n  },", 1)[0]
        out[name] = set(re.findall(r"^\s+'([^']+)':", body, flags=re.M))
    return out


def used_keys() -> set[str]:
    keys: set[str] = set()
    for js in ASSETS.glob("*.js"):
        if js.name == "i18n.js":
            continue
        keys |= set(re.findall(r"\bt\('([a-zA-Z0-9_.\-]+)'", js.read_text(encoding="utf-8")))
    html = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    keys |= set(re.findall(r'data-i18n(?:-title|-label)?="([^"]+)"', html))
    # Clés choisies par une expression conditionnelle : t(cond ? 'a' : 'b')
    for js in ASSETS.glob("*.js"):
        for a, b in re.findall(r"t\([^()]*\?\s*'([\w.]+)'\s*:\s*'([\w.]+)'", js.read_text(encoding="utf-8")):
            keys |= {a, b}
    for prefix, suffixes in DYNAMIC.items():
        keys |= {prefix + s for s in suffixes}
    return keys


def test_both_languages_have_the_same_keys():
    d = dictionaries()
    assert set(d) == {"en", "fr"}
    assert d["en"] - d["fr"] == set(), "clés manquantes en français"
    assert d["fr"] - d["en"] == set(), "clés manquantes en anglais"


def test_every_used_key_is_defined():
    d = dictionaries()
    missing = sorted(k for k in used_keys() if k not in d["en"])
    assert missing == []


def test_no_french_left_in_ui_code():
    """Hors commentaires, les modules de l'interface ne contiennent plus de texte français en dur."""
    accents = re.compile(r"[àâçéèêëîïôûùœÀÂÇÉÈÊÎÔÛ]")
    offenders = []
    for js in ASSETS.glob("*.js"):
        if js.name == "i18n.js":
            continue
        for n, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"/\*.*?\*/", "", line)
            code = code.split("//", 1)[0] if "://" not in code else code
            if line.strip().startswith(("*", "/*")):
                continue
            if accents.search(code):
                offenders.append(f"{js.name}:{n}: {line.strip()[:80]}")
    assert offenders == []
