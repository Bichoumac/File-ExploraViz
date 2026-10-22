"""Catégories de fichiers et mapping extension → catégorie par défaut."""

from __future__ import annotations

# Ordre fixe : l'indice sert d'identifiant numérique dans l'index en mémoire.
CATEGORIES: list[tuple[str, str]] = [
    ("photo", "Photos"),
    ("video", "Vidéos"),
    ("audio", "Audio"),
    ("document", "Documents texte"),
    ("spreadsheet", "Classeurs"),
    ("presentation", "Présentations"),
    ("pdf", "PDF"),
    ("archive", "Archives"),
    ("code", "Code"),
    ("executable", "Exécutables/Images disque"),
    ("other", "Autres"),
]

CATEGORY_IDS: list[str] = [cid for cid, _ in CATEGORIES]
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)
CATEGORY_INDEX: dict[str, int] = {cid: i for i, cid in enumerate(CATEGORY_IDS)}
OTHER = CATEGORY_INDEX["other"]
NCAT = len(CATEGORIES)

DEFAULT_MAPPING: dict[str, list[str]] = {
    "photo": [
        "jpg", "jpeg", "jpe", "png", "gif", "bmp", "tif", "tiff", "webp", "heic", "heif", "avif",
        "jxl", "raw", "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "orf", "rw2",
        "raf", "srw", "pef", "x3f", "svg", "ico", "psd", "xcf", "kra",
    ],
    "video": [
        "mp4", "m4v", "mkv", "avi", "mov", "wmv", "flv", "webm", "mpg", "mpeg", "m2ts", "mts",
        "ts", "3gp", "vob", "ogv", "divx", "rm", "rmvb", "asf",
    ],
    "audio": [
        "mp3", "flac", "wav", "aac", "ogg", "oga", "m4a", "wma", "opus", "aiff", "aif", "alac",
        "ape", "mid", "midi", "amr", "dsf", "m4b",
    ],
    "document": [
        "doc", "docx", "docm", "dot", "dotx", "odt", "ott", "rtf", "txt", "md", "markdown", "rst",
        "tex", "pages", "wpd", "wps", "epub", "mobi", "azw3", "djvu", "nfo",
    ],
    "spreadsheet": ["xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "ods", "ots", "csv", "tsv", "numbers"],
    "presentation": ["ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "odp", "otp", "key"],
    "pdf": ["pdf"],
    "archive": [
        "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz", "tbz2", "xz", "txz", "zst", "lz",
        "lzma", "lz4", "cab", "arj", "z",
    ],
    "code": [
        "py", "pyw", "ipynb", "js", "mjs", "cjs", "tsx", "jsx", "vue", "svelte", "java", "kt",
        "kts", "scala", "c", "h", "cpp", "cc", "cxx", "hpp", "hh", "cs", "go", "rs", "rb", "php",
        "pl", "pm", "lua", "r", "swift", "m", "mm", "dart", "sh", "bash", "zsh", "fish", "ps1",
        "psm1", "bat", "cmd", "html", "htm", "css", "scss", "sass", "less", "json", "yaml", "yml",
        "toml", "ini", "cfg", "conf", "xml", "sql", "gradle", "cmake", "mk", "dockerfile",
    ],
    "executable": [
        "exe", "msi", "dll", "so", "dylib", "sys", "com", "app", "apk", "aab", "ipa", "deb", "rpm",
        "appimage", "flatpak", "snap", "jar", "dmg", "iso", "img", "bin", "cue", "vmdk", "vdi",
        "vhd", "vhdx", "qcow2", "ova", "ovf",
    ],
}
# Remarque : « ts » est ambigu (vidéo MPEG-TS ou TypeScript) ; il est rangé en vidéo.
# Une extension ne peut appartenir qu'à une seule catégorie (vérifié par config.py).


def normalize_ext(ext: str) -> str:
    return ext.strip().lower().lstrip(".")


def ext_of(name: str) -> str:
    """Extension en minuscules sans le point ; '' pour « .bashrc » ou un nom sans point."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return ""
    return name[dot + 1 :].lower()


def build_ext_map(mapping: dict[str, list[str]]) -> dict[str, int]:
    """Extension → indice de catégorie. La première catégorie (dans l'ordre fixe) l'emporte."""
    result: dict[str, int] = {}
    for cid in CATEGORY_IDS:
        for ext in mapping.get(cid, []):
            result.setdefault(normalize_ext(ext), CATEGORY_INDEX[cid])
    return result
