# File ExploraViz — notes for Claude

Self-hosted, **read-only** file scanner + explorer/stats/treemap (FastAPI + numpy, vanilla JS). User docs: README.md; formats/API: docs/ARCHITECTURE.md (keep both in sync with changes).

## Commands
- Tests: `pytest` (Windows: `.venv/Scripts/python.exe -m pytest`). POSIX/Linux-only tests (symlinks, perms, real `statx`) skip on Windows; CI (ubuntu) runs them.
- Run: `FEV_CONFIG_DIR=./config FEV_CACHE_DIR=./cache FEV_DATA_DIR=./data PORT=8080 AUTH_DISABLED=true python -m app` (data = sub-folders of FEV_DATA_DIR = roots). Test data: `scripts/gen_tree.py`.
- Bench (1M synthetic files, all API routes incl. search modes): `python scripts/bench.py --files 1000000` (`PYTHONIOENCODING=utf-8` on Windows).
- JS has no build/lint: check with `node --input-type=module --check < file.js`.

## Layout
`app/`: `scanner.py` (scandir walk → streamed `index_<root>.yaml`), `fsstat.py` (stat + birth time: `statx` via ctypes on Linux, `st_birthtime` elsewhere), `yamlio.py` (atomic writes, index rows as JSON lines), `store.py` (in-RAM numpy index, filters, sort, search, LRU caches), `aggregates.py` (stats, treemap), `scan_manager.py`, `scheduler.py`, `auth.py`, `config.py` (Pydantic config.yaml), `i18n.py` (API messages), `api/` (routers; `deps.py` = `file_filter()` query params → `FileFilter`, `Principal`, RBAC `restrict`). `web/assets/*.js` ES modules, `i18n.js` UI strings. `tests/`.

## Invariants
- Never write under `/data`; never open file contents (list + stat only).
- Store: folders in pre-order → subtree of `d` = `[d, dir_end[d])`; files grouped by folder → `[dir_fstart[d], dir_fstart[dir_end[d]])`. Filters are slices/masks, keep them vectorised (target < 200 ms at 1M files).
- `Store` is immutable after build (scan builds a new one and swaps the reference); caches keyed by the frozen `FileFilter` (add new filter fields there + in `deps.file_filter` + `FILTER_KEYS` in `web/assets/util.js`).
- Viewer permissions = `FileFilter.access` folder ranges; out-of-scope folder must look exactly like a missing one.
- Index row: `[name, ext, category, size, mtime, ctime, btime|null]`; 6-col old indexes must still load. In the Store/API, `ctime` = **creation date** (btime if known else st_ctime), `birth`/`ctime_birth` says which. API names kept for link compatibility.
- Search (`Store.name_mask(q, scope, mode)`): scope `file|folder|path`, mode `text|regex`, case-insensitive. Text = substring, or glob if `*`/`?` present (`[...]` classes only then); glob/substring run as one `re` pass over NUL-joined lowercase text (`_joined`/`_match_entries`). Folder scope → subtree diff-array; roots never match. Path scope rebuilds `dir/name` per folder (no path index in RAM). User regex: `regex` module, per entry, `timeout` + `concurrent=True`, total `REGEX_BUDGET_S` → `SearchTimeout` → 400 `regex_timeout`; invalid → 400 `bad_pattern`. Never lowercase a regex pattern.

## Conventions
- Code comments, docstrings and log messages in **French**; UI/API text in **en + fr**.
- UI strings: add to both `en` and `fr` in `web/assets/i18n.js`; no accented French in other JS (test). Keys built dynamically (`t(\`x.${v}\`)`) must be listed in `DYNAMIC` in `tests/test_i18n_keys.py`. Use `esc()` for any name injected into HTML.
- API errors: `raise ApiError(status, "key", **params)` with the key in `app/i18n.py` (en+fr).
- Ruff line length 130, Python ≥ 3.12, LF endings. Pin deps exactly in `requirements.txt` (binary wheels for amd64+arm64; image target < 200 MB).
- Update README (features, limitations, roadmap, perf) and ARCHITECTURE when behaviour/format/API changes; README test count in Development.

## Release
Bump `app/__init__.py` `__version__` + `pyproject.toml` (+ README example `version=`/`FEV_TAG=` lines), merge to `main` (→ `:edge`), then `git tag vX.Y.Z && git push origin vX.Y.Z` → CI tests and publishes `ghcr.io/bichoumac/file-exploraviz:X.Y.Z, X.Y, X, latest`. Downgrades must keep working with newer config/index files where possible.
