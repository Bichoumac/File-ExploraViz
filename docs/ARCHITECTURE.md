# File ExploraViz architecture

Short document: modules, exact format of the YAML files, API schema.
Deviations from the original specification are listed at the end (§ 6).

## 1. Overview

```
             ┌────────────── container (1 process, uvicorn, 1 worker) ─────────────┐
 browser   ──┤ FastAPI  ── /api/* ──►  Store (numpy, in RAM)  ◄── atomic swap ────┐ │
  (vanilla   │   │                        ▲                                       │ │
   JS +      │   └── /  (static web/)     │ loaded once at startup                │ │
   ECharts + │                            │                                       │ │
   Canvas)   │  Scheduler (asyncio) ──► ScanManager ──► scan thread (nice 19,    │ │
             │                              │            ionice idle)  ──────────┘ │
             └──────────────────────────────┼─────────────────────────────────────┘
                     /data/* (ro) ◄─────────┘──► /cache/*.yaml  /config/config.yaml
```

* **No disk read per HTTP request**: requests query an immutable `Store` (columnar numpy arrays).
  A scan builds a new `Store` and swaps it in with a plain reference assignment.
* **~0 % CPU when idle**: no polling; the scheduler sleeps (`asyncio.wait_for`) until the next due
  time; the SSE stream only runs while a browser is connected.

## 2. Modules (`app/`)

| Module | Purpose |
|---|---|
| `settings.py` | Environment variables (`PORT`, `AUTH_DISABLED`, …) and the `/config`, `/cache`, `/data` paths. |
| `logging_setup.py` | Structured `key=value` logs (logfmt), `LOG_LEVEL`. |
| `categories.py` | The 11 categories and the default extension → category mapping. |
| `config.py` | Pydantic schema of `config.yaml`, atomic load/save, detection of roots under `/data`. |
| `pathsec.py` | Relative path normalisation, confinement to the roots (path traversal, symbolic links). |
| `yamlio.py` | Atomic writes, `CSafeLoader`/`CSafeDumper`, **streaming** index writer and reader. |
| `scanner.py` | Iterative `os.scandir` walk of a root → `index_<root>.yaml` (streamed). |
| `fsstat.py` | Per-file `stat` with the real creation date: `statx(2)` through `ctypes` on Linux, `st_birthtime` elsewhere. |
| `store.py` | In-memory index (numpy), filters, sorting, pagination, name search, LRU caches. |
| `aggregates.py` | Statistics (categories, extensions, years, histogram, top N, duplicates…) and treemap tree. |
| `scan_manager.py` | "One scan at a time" lock, low-priority thread, progress, cancellation, summary/history writing, Store swap. |
| `scheduler.py` | asyncio loop: interval (15 min by default), optional off-peak window. |
| `auth.py` | bcrypt, HMAC-signed session tokens, brute-force throttling. |
| `i18n.py` | API messages in English/French (`ApiError`, `tr`, `lang_of`). |
| `entrypoint.py` | Container entry point: applies `PUID`/`PGID`, then starts uvicorn. |
| `api/*.py` | FastAPI routers (auth, files, stats, treemap, scan, config, users). `deps.py` resolves the session into a `Principal` (username, role, allowed folders). |
| `main.py` | Application factory, lifespan, GZip, static files. |

## 3. YAML file formats (`/cache`)

### 3.1 `index_<root>.yaml` — detailed index, multi-document, streamed

A header document, then **one document per folder** (pre-order DFS, children sorted), then a footer
document. Each file is a flow sequence `[name, extension, category, size, mtime, ctime, btime]`
(dates in epoch seconds). `ctime` is the last inode change (`st_ctime`); `btime` is the real
creation date, or `null` when the file system does not report it. Indexes written before `btime`
existed have 6 columns and are still read (no birth time).

```yaml
# File ExploraViz - index détaillé (généré automatiquement, ne pas modifier)
---
format: fev-index/1
root_id: photos
root_name: Photos
root_path: /data/photos
started_at: '2026-10-22T10:00:00+02:00'
columns: [name, ext, category, size, mtime, ctime, btime]
---
dir: ""
files:
- ["IMG_0001.jpg", "jpg", "photo", 2345678, 1690000000, 1690000000, 1689990000]
- ["notes.txt", "txt", "document", 120, 1690000000, 1690000000, null]
---
dir: "2023/holidays"
errors:
- ["unreadable_file.jpg", "[Errno 13] Permission denied"]
files: []
---
dir: "2023/private"
error: "[Errno 13] Permission denied"
files: []
---
footer: {"finished_at": "2026-10-22T10:03:12+02:00", "status": "ok", "files": 2, "dirs": 3, "bytes": 2345798, "inaccessible": 2, "symlinks_skipped": 0, "birth_times": 1, "duration_s": 192.4}
```

* Relative path of a file = `dir` + `/` + `name`.
* File rows are written as **JSON, which is a subset of YAML** (characters YAML forbids are escaped
  as `\uXXXX`): any YAML parser can read the file (`CSafeLoader` is the fallback), but the
  application reads it line by line with `json.loads`, **20 times faster** than libyaml (measured:
  0.4 s vs 8.6 s for 200,000 rows).
* The category is written for information; at load time it is recomputed from the extension so
  that a mapping change applies without rescanning.

### 3.2 `summary.yaml` — pre-computed aggregates (what the UI loads by default)

```yaml
format: fev-summary/1
generated_at: '2026-10-22T10:03:15+02:00'
scan: {started_at: ..., finished_at: ..., duration_s: 195.2, status: ok, trigger: planifié, roots: [photos, documents]}
totals: {files: 812345, dirs: 40211, bytes: 4398046511104, inaccessible: 3}
roots:
  photos: {name: Photos, files: 500000, dirs: 20000, bytes: ..., scanned_at: ..., duration_s: ..., status: ok}
stats:            # same object as the GET /api/stats response (no filter)
  totals: {...}
  categories: [{id: photo, label: Photos, files: 1234, bytes: 5678}, ...]
  extensions_by_size: [...]       # top 20
  extensions_by_count: [...]      # top 20
  size_histogram: [...]           # log buckets
  years: [...]  age: [...]
  largest_files: [...]            # top 50
  largest_dirs: [...]             # top 20
  duplicates: {groups, files, recoverable_bytes, top: [...]}
  empty_dirs: {count, sample}  inaccessible: {count, sample}
stats_by_root: {photos: {...}, documents: {...}}
trees:            # aggregated tree for the treemap, 3 levels, ~3000-node budget per root
  photos: {type: dir, name: Photos, path: "", size: ..., files: ..., cats: {photo: ...}, children: [...]}
```

### 3.3 `history/summary_<YYYYMMDDTHHMMSS>.yaml`

Copy of `summary.yaml` without `trees` and `stats_by_root` (to stay small). The last N are kept
(`scan.history_size`).

### 3.4 `/config/config.yaml`

```yaml
version: 1
roots:
- {id: photos, folder: photos, name: Photos, enabled: true}
scan:
  interval_minutes: 15          # 0 = no automatic scan
  offpeak: {enabled: false, start: '01:00', end: '06:00'}
  exclusions: ['.zfs', '@eaDir', '#recycle', '.Trash*', 'node_modules', '*.tmp', ...]
  follow_symlinks: false
  max_depth: 0                  # 0 = unlimited
  history_size: 30
ui: {page_size: 100}
categories: {photo: [jpg, jpeg, ...], video: [...], ...}
auth:
  users:
  - {username: admin, password_hash: $2b$12$..., role: admin, all_folders: false, scopes: []}
  - {username: alice, password_hash: $2b$12$..., role: viewer, all_folders: false,
     scopes: [{root: photos, path: 2023}, {root: documents, path: ''}]}
  session_hours: 168
  secret: <random>
# Version 1.0 stored a single account (username/password_hash): it is migrated to an admin user.
```

## 4. In-memory index (`Store`)

* Folders are numbered in **pre-order**: the subtree of folder `d` is the range `[d, end[d])`, and
  since files are grouped by folder, its files are a contiguous range too. The "folder + sub-folders"
  filter is therefore a slice, not a walk.
* numpy columns: `size`, `mtime`, `ctime` (int64), `birth` (bool), `ext_id` (int32), `cat` (uint8),
  `dir` (int32). `ctime` holds the **creation date**: `btime` when known (`birth` true), otherwise
  `st_ctime`.
* Names: one big string plus an offset array (≈ 25 bytes/file instead of ~70 for Python `str`).
* Search (`Store.name_mask`, cached per query, scope and mode): lower-cased search texts are built
  once — file names and folder names, each joined with NUL separators, plus folder paths.
  * **Substring and wildcards** are translated to a regular expression that cannot cross a NUL and
    run in **one pass** over the whole text; match positions are mapped back to entries with
    `searchsorted`.
  * **Folder scope** matches folder names, then marks each matching folder's subtree with a
    difference array (`+1` at `d`, `-1` at `end[d]`, cumulative sum) and maps it to files.
  * **Path scope** rebuilds `folder/name` texts folder by folder (prefix + names already joined),
    so no full-path index is kept in memory.
  * **Regular expressions** (user syntax, which could cross separators) are tested one entry at a
    time with the `regex` module, under a 10 s total budget (`REGEX_BUDGET_S`), releasing the GIL.
* Per-folder aggregates (size, count, size per category, max mtime) computed with cumulative sums,
  on demand then cached, for the treemap.
* LRU caches: selection per filter, sort per (filter, sort), statistics per filter; invalidated with
  every new `Store`.
* **Folder permissions**: a viewer's allowed folders are turned into merged folder ranges
  (`Store.access_ranges`) stored in `FileFilter.access`; `Store.select` masks files and folders
  outside them, so every route and cache honours them. `visible(d)` lets a viewer browse the
  parents of an allowed folder; a folder that is neither allowed nor a parent behaves as missing.

## 5. REST API (OpenAPI: `/api/docs`)

Every `/api/*` route requires a session, except `auth/state`, `auth/login` and `auth/setup`.
Routes marked **admin** answer 403 to viewers; data routes are restricted to a viewer's folders.

| Method | Route | Description |
|---|---|---|
| GET | `/healthz` | Healthcheck (no auth). |
| GET | `/api/auth/state` | `{auth_disabled, setup_required, user}` |
| POST | `/api/auth/setup` | Creates the initial account (only when none exists). |
| POST | `/api/auth/login` / `/api/auth/logout` | Session through the `fev_session` cookie. |
| POST | `/api/auth/password` | Change one's own password. |
| GET/POST | `/api/users` | **admin** — list / create accounts. |
| PUT/DELETE | `/api/users/{username}` | **admin** — change role and folders / delete. |
| POST | `/api/users/{username}/password` | **admin** — reset a viewer's password. |
| GET | `/api/roots` | Configured and detected roots, with totals. |
| GET | `/api/tree?root=&path=` | Direct sub-folders (collapsible tree). |
| GET | `/api/files?root=&path=&recursive=&ext=&category=&from=&to=&date_field=&min_size=&max_size=&q=&q_scope=&q_mode=&sort=&page=&page_size=` | Paginated list. |
| GET | `/api/files/export.csv?…` | CSV export (same filters). |
| GET | `/api/extensions?root=&prefix=` | Extension autocompletion. |
| GET | `/api/stats?…` | Statistics (same filters). |
| GET | `/api/stats/history` | Evolution (scan history). |
| GET | `/api/treemap?root=&path=&depth=` | Aggregated tree for the treemap. |
| POST | `/api/scan` (`{"root": null\|"id"}`) | **admin** — starts a scan; 409 if one is running. |
| POST | `/api/scan/cancel` | **admin** — cancels the running scan. |
| GET | `/api/scan/status` | State, last scan, next scan. |
| GET | `/api/scan/progress` | SSE stream, `event: progress`. |
| GET/PUT | `/api/config` | **admin** — configuration (without secrets or accounts). |
| POST | `/api/cache/clear` | **admin** — clears the cache, then starts a scan. |

Common parameters: `ext` and `category` are comma-separated lists; `from`/`to` use `YYYY-MM-DD`;
`sort` is `name|path|ext|category|size|mtime|ctime`, prefixed with `-` for descending order;
`ctime` (also in `date_field`) is the **creation date** — the real one when the file system reports
it, otherwise the last inode change; each row says which with `ctime_birth` (`true` = real).
`q` is a substring, or a glob when it contains `*` or `?` (`*`, `?`, `[abc]`, `[a-z]`, `[!abc]`);
`q_mode=regex` makes it a regular expression (`regex` module syntax). `q_scope` chooses what it is
compared with: `file` (file name, default), `folder` (name of any parent folder; a match brings in
the folder's whole subtree, roots excluded) or `path` (path relative to the root). Always
case-insensitive. An invalid pattern answers 400 `bad_pattern`; a regular expression still running
after 10 s answers 400 `regex_timeout`.

API messages are returned in English or French depending on the `fev_lang` cookie set by the UI
(English by default); errors are `{"detail": "…", "code": "stable_key"}` (see `app/i18n.py`).

## 6. Deviations from the specification

1. **numpy** for the in-memory index: this is what makes the < 200 ms target for 1 M files
   achievable. It adds about 48 MB to the image.
2. **Index rows as JSON** (strict subset of YAML) and a line-by-line reader;
   `CSafeLoader`/`CSafeDumper` are still used for everything else and as a fallback (§ 3.1).
3. **Home-made asyncio scheduler** instead of APScheduler: one dependency less, no useless wake-up,
   and the off-peak logic is simple to write.
4. **Vanilla JavaScript frontend (ES modules)**, no Alpine.js; ECharts is bundled in the image to
   work offline on a NAS.
5. **Thread rather than process** for the scan: `os.nice(19)` and `ioprio_set` apply per thread on
   Linux, and the new index is handed over in memory without copying.
6. **First scan at startup** when nothing is cached.
7. **`ADMIN_USER`/`ADMIN_PASSWORD`** only **initialise** the account: a password changed in the UI
   is not overwritten on restart. Likewise `SCAN_INTERVAL_MINUTES` is only an initial value.
8. **"Creation date"**: the real birth time read with `statx(2)` when the file system reports it;
   otherwise `st_ctime`, the last inode change, flagged as such (`ctime_birth: false`).
9. **Duplicates**: grouped by (case-insensitive name, size), non-empty files only.
10. Extra routes: `/api/auth/state`, `/api/auth/setup`, `/api/auth/password`, `/api/extensions`,
    `/api/cache/clear`.
11. **Privilege dropping in Python** (`app/entrypoint.py`) instead of `gosu`/`setpriv`: no system
    dependency, compatible with `read_only: true` and `cap_drop: ALL`.
12. **CSV export** with `;` and a UTF-8 BOM, to open directly in French Excel.
