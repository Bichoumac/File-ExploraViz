# File ExploraViz

A self-hosted web application that **periodically scans your directories in read-only mode**,
stores the results in **YAML** files and presents them as a filterable file explorer, statistics
and a **WinDirStat-style treemap**. It is built for a NAS: the scan runs at low priority
(`nice 19`, *idle* I/O class) and browsing never touches the disk again.

* Deploy on **TrueNAS SCALE + Dockge** or **standalone Docker**, with the same image.
* Mandatory login (bcrypt, signed `HttpOnly`/`SameSite=Strict` cookie, brute-force protection).
* Light/dark theme, dedicated **mobile layout**, no Internet dependency at runtime.
* The user interface is currently in **French** (see [Roadmap](#roadmap)).

| Explorer | Statistics |
|---|---|
| ![Explorer](docs/screenshots/explorateur.png) | ![Statistics](docs/screenshots/statistiques.png) |
| **Treemap** | **Treemap (dark theme)** |
| ![Treemap](docs/screenshots/treemap.png) | ![Dark treemap](docs/screenshots/treemap-sombre.png) |

| **Mobile: Explorer** | **Mobile: Treemap (block tapped)** |
|---|---|
| <img src="docs/screenshots/mobile-explorateur.png" alt="Explorer on a phone" width="300"> | <img src="docs/screenshots/mobile-treemap.png" alt="Treemap on a phone" width="300"> |

*Screenshots taken on a synthetic dataset (`scripts/gen_tree.py`).*

---

## Table of contents

1. [Features](#features)
2. [Installing on TrueNAS SCALE with Dockge](#installing-on-truenas-scale-with-dockge)
3. [Standalone installation (Docker Compose)](#standalone-installation-docker-compose)
4. [Configuration](#configuration)
5. [Backup](#backup)
6. [Measured performance](#measured-performance)
7. [Troubleshooting](#troubleshooting)
8. [Development](#development)
9. [Known limitations](#known-limitations)
10. [Roadmap](#roadmap)
11. [Design decisions](#design-decisions)
12. [License](#license)

---

## Features

**Scanning**
* Automatic every 15 minutes (configurable; 0 = disabled), with an optional off-peak window.
* "Scan now" button (all roots or a single root) and "Cancel scan".
* Live progress (Server-Sent Events): files, folders, volume, current folder, speed, and a
  percentage estimated from the previous scan (or from remaining folders, flagged as an estimate).
* One scan at a time. Glob exclusions, maximum depth, symbolic links not followed by default (and
  never outside the roots). Permission errors are logged, counted and the scan carries on.
* **No scan on page load**: the UI reads the latest result, loaded into memory once.

**Explorer**: summary banner (files, folders, volume, clickable per-category breakdown),
collapsible folder tree, breadcrumb, combinable filters (modification or creation date with
shortcuts, extensions with autocompletion, categories, size range, substring or glob name search),
filters stored in the URL (shareable links), sortable columns, server-side pagination, virtualised
table, CSV export.

**Statistics**: donut per category (volume/count), top 20 extensions, size histogram (log buckets),
modification years, file age, top 50 files and top 20 folders, most populated folders, probable
duplicates (same name + same size) with reclaimable space, empty files and folders, inaccessible
items, hidden files, suspicious dates, evolution over time. Every statistic follows the Explorer
filters.

**Treemap**: *squarified* layout, Canvas rendering, WinDirStat-like *cushion* shading (optional),
colour per category, clickable legend to hide a category, tiny items grouped together, tooltip
(path, size, % of parent, date), click to zoom, double-click to open the folder in the Explorer,
side panel of sub-folders synchronised with the map.

**Mobile layout** (screens 760 px wide or less): bottom tab bar; collapsible folder tree and
filters panel with an active-filter counter; files shown as cards with a sort drop-down;
horizontally scrolling categories; touch-friendly treemap (a tap opens a sheet with "Zoom" and
"Explorer" actions); 16 px inputs to avoid iOS auto-zoom; notch-aware (`safe-area`). On tablets the
columns stack.

**Configuration**: roots detected under `/data`, scheduling, exclusions, extension → category
mapping (applied without rescanning), options, password and session lifetime, "clear cache and
rescan".

---

## Installing on TrueNAS SCALE with Dockge

Procedure for TrueNAS SCALE 24.10 ("Electric Eel") or later, which ships Docker natively, with
[Dockge](https://github.com/louislam/dockge) already installed (**Apps → Discover Apps → Dockge**).

The examples use the pool `tank`, the datasets `tank/photos` and `tank/documents`, and UID/GID
`3000`. **Replace them everywhere with your own values.** Commands are typed in **System → Shell**
(or over SSH) with the administrator account, hence `sudo`.

**What we are going to set up**

| Path on the NAS | Purpose | Rights of the `exploraviz` user |
|---|---|---|
| `/mnt/tank/photos`, `/mnt/tank/documents` | data to analyse | **read-only** (ACL), plus `:ro` mount |
| `/mnt/tank/apps/file-exploraviz/src` | source code (used to build the image) | none needed |
| `/mnt/tank/apps/file-exploraviz/config` | `config.yaml` (settings, account) | read/write |
| `/mnt/tank/apps/file-exploraviz/cache` | scan results (YAML) | read/write |

### Step 1 — Create a dedicated group and user

1. **Credentials → Groups → Add**
   * **GID**: keep the suggested value and **write it down** (here `3000`);
   * **Name**: `exploraviz`;
   * leave the rest as is, then **Save**.
2. **Credentials → Users → Add** ("Local Users" on some versions)
   * **Full Name**: `File ExploraViz`, **Username**: `exploraviz`;
   * **Disable Password**: on (this account never logs in);
   * **UID**: keep the suggested value and **write it down** (here `3000`);
   * **Create New Primary Group**: unchecked, then **Primary Group**: `exploraviz`;
   * **Home Directory**: `/var/empty`, **Shell**: `nologin`;
   * **SMB User**: unchecked;
   * **Save**.
3. Check in the Shell:
   ```sh
   id exploraviz
   # uid=3000(exploraviz) gid=3000(exploraviz) groups=3000(exploraviz)
   ```

### Step 2 — Grant read access on every dataset to analyse

Repeat for **each** dataset (`tank/photos`, then `tank/documents`…):
**Datasets** → click the dataset → **Permissions** card → **Edit**. The editor you get depends on
the dataset's ACL type.

**A. "Edit ACL" editor, NFSv4 type** (usual for SMB-shared datasets)

1. **Add Item**, then fill in the entry:
   * **Who**: `Group`; **Group**: `exploraviz`;
   * **ACL Type**: `Allow`;
   * **Permissions Type**: `Basic`; **Permissions**: `Read`;
   * **Flags Type**: `Basic`; **Flags**: `Inherit`.
2. Tick **Apply permissions recursively** (and confirm), and **Apply permissions to child
   datasets** if the dataset has child datasets.
3. **Save Access Control List**. Applying can take a while on a large dataset.

**B. "Edit ACL" editor, POSIX type** ("Generic" datasets)

1. **Add Item**: **Who**: `Group`, **Group**: `exploraviz`, **Permissions**: `Read` + `Execute`.
2. **Add Item** a second time with the same values and **Default** ticked, so that files created
   later inherit it.
3. Tick **Apply permissions recursively**, then **Save Access Control List**.

**C. "Unix Permissions Editor"** (no ACL on the dataset)

* Either switch to an ACL with the **Set ACL** (or **Add ACL**) button and follow case B;
* or, simpler but less strict, give **Other** the **Read** and **Execute** rights, applied
  recursively. Every local account on the NAS can then read this data.

**Check in the Shell** (for each dataset):

```sh
cd /
sudo -u exploraviz ls /mnt/tank/photos | head                    # must list the content
sudo -u exploraviz touch /mnt/tank/photos/write-test             # must answer "Permission denied"
sudo -u exploraviz find /mnt/tank/photos > /dev/null             # prints nothing if everything is readable
```

The last command lists folders that are still unreadable. Fix them by going through step 2 again,
making sure **Apply permissions recursively** is ticked.

### Step 3 — Prepare the folders and fetch the code

(Optional but recommended: first create the dataset `tank/apps/file-exploraviz` through
**Datasets → Add Dataset**, preset **Generic**, so it can be backed up with snapshots.)

```sh
sudo mkdir -p /mnt/tank/apps/file-exploraviz
cd /mnt/tank/apps/file-exploraviz
sudo git clone https://github.com/Bichoumac/File-ExploraViz.git src
sudo mkdir -p config cache
sudo chown -R 3000:3000 config cache          # UID:GID from step 1
```

If `git` is not available, download the archive instead of `git clone`:

```sh
curl -L https://github.com/Bichoumac/File-ExploraViz/archive/refs/heads/main.tar.gz | sudo tar xz
sudo mv File-ExploraViz-main src
```

### Step 4 — Build the Docker image

```sh
sudo docker build -t file-exploraviz:local /mnt/tank/apps/file-exploraviz/src
sudo docker image ls file-exploraviz          # one line "file-exploraviz  local  …  ~191MB"
```

Allow 1 to 3 minutes: the NAS downloads `python:3.12-slim` and the dependencies, so it needs
Internet access. A "legacy builder is deprecated" warning may show up; it is harmless.

> **Why build from the Shell rather than in Dockge?** Dockge runs `docker compose` from inside its
> own container, which does not necessarily see the `src` folder. Building the image once from the
> Shell means the Dockge stack only has to use it.

### Step 5 — Create the stack in Dockge

1. In Dockge, click **+ Compose**.
2. **Stack Name**: `file-exploraviz`.
3. Replace the whole content of the **compose.yaml** editor with the file below, then **adapt the
   `volumes` lines** to your datasets:

   ```yaml
   services:
     file-exploraviz:
       image: file-exploraviz:local      # built in step 4
       pull_policy: never                # local image: do not look for it on Docker Hub
       container_name: file-exploraviz
       restart: unless-stopped
       ports:
         - "${HOST_PORT:-8080}:8080"
       environment:
         PUID: ${PUID}
         PGID: ${PGID}
         SUPPLEMENTARY_GIDS: ${SUPPLEMENTARY_GIDS:-}
         TZ: ${TZ:-Europe/Paris}
         ADMIN_USER: ${ADMIN_USER:-admin}
         ADMIN_PASSWORD: ${ADMIN_PASSWORD:-}
         SCAN_INTERVAL_MINUTES: ${SCAN_INTERVAL_MINUTES:-15}
         LOG_LEVEL: ${LOG_LEVEL:-INFO}
       volumes:
         # Application data: absolute paths (see the note below)
         - /mnt/tank/apps/file-exploraviz/config:/config
         - /mnt/tank/apps/file-exploraviz/cache:/cache
         # Datasets to analyse: one sub-folder of /data per dataset, ALWAYS :ro
         - /mnt/tank/photos:/data/photos:ro
         - /mnt/tank/documents:/data/documents:ro
       read_only: true
       tmpfs:
         - /tmp:size=64m,mode=1777
       security_opt:
         - no-new-privileges:true
       cap_drop:
         - ALL
       cap_add:
         - CHOWN
         - DAC_OVERRIDE
         - FOWNER
         - SETUID
         - SETGID
       # mem_limit: 768m
       # cpus: "1.0"
   ```

   * **One line per dataset**: `path-on-the-NAS:/data/<name>:ro`. `<name>` becomes the root name in
     the UI (it can be renamed later in **Configuration**).
   * **Absolute paths** for `config` and `cache`: Dockge runs `docker compose` from its own
     container, so a relative path such as `./config` could point somewhere other than the stack
     folder.
   * The right-hand port (`8080`) stays as is. To publish another port, change `HOST_PORT`.

4. In the **.env** editor (below the compose editor in Dockge), paste:

   ```ini
   PUID=3000
   PGID=3000
   HOST_PORT=8080
   TZ=Europe/Paris
   ADMIN_USER=admin
   ADMIN_PASSWORD=
   SCAN_INTERVAL_MINUTES=15
   ```

   * `PUID`/`PGID`: the UID and GID written down in step 1.
   * Empty `ADMIN_PASSWORD` (recommended): the **first visitor** of the page creates the account, so
     do it right after deploying. If you fill it in, the password stays in clear text in Dockge's
     `.env`: empty it after the first start (it is only used for initialisation).
   * Is the ACL granted to a group other than the user's primary group? Add
     `SUPPLEMENTARY_GIDS=<gid>` (several values separated by commas).

5. Click **Deploy**. The stack logs should show, in this order (log messages are in French):
   ```
   msg="exécution en tant que 3000:3000"
   msg="nouvelle racine détectée" root=photos folder=photos
   msg="File ExploraViz démarré" version=1.0.0 port=8080
   ```
   then, about ten seconds later, `msg="scan terminé"` (the first scan starts by itself).
6. Open `http://<nas-ip>:8080` and create the account (or log in with `ADMIN_PASSWORD`).

### Step 6 — Check that everything is right

* In Dockge, the container becomes **healthy** within a minute of starting.
* The logs contain **no** warning `… est accessible en écriture : montez-le en lecture seule (:ro)`
  ("is writable: mount it read-only"). Otherwise a volume is missing `:ro`.
* **Configuration → Racines à scanner** (roots to scan): each dataset shows the **monté** (mounted)
  state.
* **Statistiques → Éléments inaccessibles** (inaccessible items) is 0. Otherwise the list shows the
  refused folders: go through step 2 again for them.

### Updating

```sh
cd /mnt/tank/apps/file-exploraviz/src
sudo git pull                                  # or download the archive again (step 3)
sudo docker build -t file-exploraviz:local .
```

Then click **Update** in Dockge. The `pull` is skipped thanks to `pull_policy: never` and the
container is recreated with the new image. Settings and results are kept, since they live in
`config/` and `cache/`. To free the old image: `sudo docker image prune -f`.

### Adding or removing a dataset

1. Grant read access on the new dataset (step 2).
2. In Dockge: **Edit**, add the line `- /mnt/tank/<dataset>:/data/<name>:ro`, then **Save** and
   **Deploy**.
3. The new root is detected at startup and included in the next scan. Each root also has its own
   **Scanner** (scan) button in **Configuration**.

To remove a dataset, delete its volume line. The root then shows as **absent** in
**Configuration**, where you can disable it.

### Uninstalling

In Dockge: **Stop**, then **Delete**. Then, in the Shell:
`sudo docker image rm file-exploraviz:local && sudo rm -rf /mnt/tank/apps/file-exploraviz`.
Finally, delete the `exploraviz` user and group (Credentials) and the ACL entries added in step 2.

### Common Dockge problems

| Message | Cause and fix |
|---|---|
| `pull access denied for file-exploraviz` | The image has not been built (step 4), or the `pull_policy: never` line is missing. |
| `bind source path does not exist: /mnt/...` | Wrong volume path: check it with `ls` in the Shell. |
| `port is already allocated` | Port 8080 is used by another app: change `HOST_PORT` in the `.env`. |
| The container keeps restarting | Read the logs. Usually `config/` is not owned by `PUID:PGID` (redo the `chown` of step 3), or `config.yaml` is invalid (the faulty key is named). |
| Empty roots or "Éléments inaccessibles" | Incomplete read rights: redo the check of step 2. |

### Variant: SMB/CIFS share from another machine

If the data is not on the NAS running Dockge, add **at the end** of the compose file (same level as
`services:`):

```yaml
volumes:
  nas-smb:
    driver: local
    driver_opts:
      type: cifs
      device: "//${SMB_HOST}/${SMB_SHARE}"
      o: "ro,username=${SMB_USER},password=${SMB_PASSWORD},vers=3.0,uid=${PUID},gid=${PGID},file_mode=0444,dir_mode=0555,noserverino"
```

Then add `- nas-smb:/data/share:ro` to the service `volumes`, and `SMB_HOST`, `SMB_SHARE`,
`SMB_USER`, `SMB_PASSWORD` to the `.env`. Use an SMB account that **only** has read access.

---

## Standalone installation (Docker Compose)

On any host with Docker Engine 24+ and the Compose v2 plugin:

```sh
git clone https://github.com/Bichoumac/File-ExploraViz.git
cd File-ExploraViz
cp .env.example .env            # PUID/PGID = `id -u` / `id -g` of a user that can read your data
$EDITOR compose.yaml            # replace the /mnt/tank/... volumes with your directories (:ro)
docker compose up -d --build
docker compose logs -f          # "File ExploraViz démarré", then "scan terminé"
```

UI: `http://localhost:8080`. Update: `git pull && docker compose up -d --build`.

Behind a reverse proxy (Traefik, Caddy, Nginx Proxy Manager…), set `FORWARDED_ALLOW_IPS` to the
proxy IP. The session cookie then gets the `Secure` attribute over HTTPS. If the proxy already
authenticates users, you may set `AUTH_DISABLED=true`.

---

## Configuration

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PUID` / `PGID` | `1000` | Runtime user and group. The container starts as root only to hand `/config` and `/cache` over to them, then drops privileges for good. |
| `SUPPLEMENTARY_GIDS` | *(empty)* | Extra groups, comma-separated (e.g. a group holding a read ACL). |
| `TZ` | `Europe/Paris` | Time zone of logs, history and the off-peak window. |
| `PORT` | `8080` | Listening port inside the container (`HOST_PORT`: port published on the host). |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / *(empty)* | Account created **on first start only**. Empty: account creation screen in the UI. |
| `SCAN_INTERVAL_MINUTES` | `15` | **Initial** interval (then editable in the UI; 0 = disabled). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. In `DEBUG`, every HTTP request is logged. |
| `AUTH_DISABLED` | `false` | `true` only behind a proxy that already authenticates. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy IPs trusted for `X-Forwarded-*`. |
| `INITIAL_SCAN` | `true` | Scan automatically at startup when nothing is cached yet. |

Advanced / development: `FEV_CONFIG_DIR`, `FEV_CACHE_DIR`, `FEV_DATA_DIR`, `FEV_WEB_DIR`.

### Files

| Path | Content |
|---|---|
| `/config/config.yaml` | Full configuration validated by a Pydantic schema: roots, scheduling, exclusions, categories, account (bcrypt password hash), session secret. |
| `/cache/summary.yaml` | Aggregates of the latest scan: totals, categories, extensions, years, top N, treemap tree. |
| `/cache/index_<root>.yaml` | Detailed index: one YAML document per folder, one line per file. |
| `/cache/history/summary_<date>.yaml` | History of the last N scans (without the treemap tree). |

The exact formats are described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Every file is
written **atomically** (temporary file, then `rename`). If `config.yaml` is invalid, the container
refuses to start and names the faulty key, e.g. `scan.interval_minutes : nombre entier attendu`
("integer expected").

To **reset the password**: stop the stack, delete the `username` and `password_hash` lines from
`config/config.yaml`, then restart. The account creation screen comes back (or `ADMIN_PASSWORD`
is applied again).

### API

Interactive OpenAPI documentation: `http://<host>:8080/api/docs` (once logged in).
Unauthenticated healthcheck: `GET /healthz`.

---

## Backup

* **`config/`**: back it up. It is small and holds the account and settings.
* **`cache/`**: optional. It is fully rebuilt by the next scan. Back up `cache/history/` if you care
  about the evolution history.

On TrueNAS, a *Periodic Snapshot Task* on the dataset holding the application folders is enough.

---

## Measured performance

Measured on 22/10/2026 on Linux (WSL2, Ubuntu 24.04, Python 3.12, development workstation) with
`scripts/bench.py` (synthetic index of **1,000,000 files** in 40,000 folders) and
`scripts/bench_scan.py`. They can be reproduced with the commands in [Development](#development).

**Memory and loading (1 M files)**

| | |
|---|---|
| Size of `index_bench.yaml` | 78 MB |
| Index loading + pre-computations at startup | 5.0 s |
| Idle RSS after loading | **316 MB** (61 MB for the bare Python/FastAPI process) |
| Measured peak (loading + requests) | 403 MB |

Rule of thumb: 60 MB + **about 260 bytes per indexed file**.

**API latency (1 M files, 100 rows per page)**

| Request | First call | Next calls (median) |
|---|---:|---:|
| List, tree order | 21 ms | 5 ms |
| List sorted by size, descending | 106 ms | 6 ms |
| List sorted by name | 40 ms | 5 ms |
| Extension + date filter | 12 ms | 5 ms |
| Category + minimum size filter | 8 ms | 5 ms |
| Substring / glob name search | 14 / 19 ms | 5 ms |
| Explorer banner | 24 ms | 4 ms |
| Full statistics (no filter / filtered) | 5 / 37 ms | 5 ms |
| Treemap (3 levels) | 48 ms | 35 ms |

Every request stays under the 200 ms target from the first call. The first call of a filter
includes computing it; the result is then cached until the next scan.

**Scan** (200,000 files in 1,939 folders, files already in the OS cache): **2.0 s, about 100,000
files/s**, 15 MB index. On a ZFS pool of spinning disks whose metadata is not in ARC, expect a few
thousand files per second on the first scan: the per-file `stat()` dominates. Later scans benefit
from the cache.

**Idle**, no browser tab open: 1 CPU tick in 5 s, about 0.2 % of one core. With a tab open, the
progress stream wakes up every 2 s.

**Docker image**: **191 MB** (measured with Docker 29.1, python:3.12-slim + slimmed-down numpy),
under the 200 MB target. Builds in 21 s. Idle container after scanning 25,000 files: 0.14 % CPU
and 61 MB RAM.

---

## Troubleshooting

**"Aucun dossier détecté sous /data" (no folder under /data) / root shown as "absent"**
The volume is not mounted under `/data/<name>`. Check the `volumes` section of the compose file,
then `docker exec file-exploraviz ls -la /data`.

**Inaccessible items / `Permission denied`**
* Check the effective identity: `docker exec file-exploraviz python -c "import os; print(os.getuid(), os.getgid(), os.getgroups())"`
  (note: `docker exec` starts a *new* process as root; the application itself runs as
  `PUID:PGID`, visible with `docker exec file-exploraviz grep -E '^(Uid|Gid)' /proc/1/status`).
* It must match `PUID`/`PGID`, and that group (or one in `SUPPLEMENTARY_GIDS`) must be in the
  dataset ACL, **applied recursively**.
* The Statistics page lists the first 100 refused items with their error message.

**Warning "… est accessible en écriture : montez-le en lecture seule (:ro)"**
Add `:ro` to the volume. The application never writes to `/data`, but a read-only mount guarantees
that no bug ever could.

**The scan is slow or loads the NAS**
* It already runs at `nice 19` with the *idle* I/O class: it yields to any other activity.
* Enable the **off-peak window** (Configuration page) and increase the interval (60 min or more).
* Add exclusions: `.zfs`, `@eaDir`, `#recycle`, `.Trash*`, `node_modules`… are excluded by
  default; add for instance application cache folders.
* Set a **maximum depth** if deep levels are not useful.
* The ZFS *snapdir* (`.zfs`) is excluded: do not make it visible.

**Memory usage on very large volumes**
* Count about 260 bytes per indexed file (plus 60 MB), and a peak of about 1.3× that value while
  reloading after a scan.
* Set a limit in the compose file (`mem_limit`) with some headroom, e.g. 768 MB for 1 to 2 M files.
* Disable useless roots, or exclude folders with huge file counts (caches, Time Machine-style
  backups).

**The container keeps restarting**
`docker compose logs` shows the cause. Most often an invalid `config.yaml` (the faulty key is
named) or a non-writable `/config` (host folder permissions).

---

## Development

```sh
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                                           # 51 tests (scanner, filters, aggregates, security, YAML, auth, API)

# Run locally on a test folder
python scripts/gen_tree.py ./data/demo --files 5000
FEV_CONFIG_DIR=./config FEV_CACHE_DIR=./cache FEV_DATA_DIR=./data PORT=8080 python -m app

# Benchmarks
python scripts/bench.py --files 1000000          # synthetic index: memory, latencies
python scripts/gen_tree.py /tmp/data/demo --files 200000 && python scripts/bench_scan.py /tmp/data/demo
```

Layout: `app/` (backend: `scanner`, `yamlio`, `store`, `aggregates`, `scan_manager`, `scheduler`,
`auth`, `config`, `api/`), `web/` (static frontend as ES modules, no build step), `tests/`,
`scripts/`, `docs/ARCHITECTURE.md` (formats and API). Source code comments and UI strings are in
French.

---

## Known limitations

**Deployment**
* **Not yet tested on a real TrueNAS/Dockge install.** The Dockge procedure was replayed with
  Docker 29.1 under WSL2 (deploy, update after rebuild, permissions); TrueNAS menu names may differ
  slightly between versions.
* **No published image**: it has to be built locally (step 4 above).
* Single process, single worker: the scan thread shares the Python GIL with the API, so requests
  are a little slower while a scan runs.

**Data and accuracy**
* **Sizes are apparent sizes** (`st_size`), not space used on disk: ZFS compression,
  deduplication, sparse files and snapshots are not reflected. A hard-linked file is counted once
  for each of its paths.
* **"Creation date" is `st_ctime` on Linux** (last inode change), not the real birth time.
* **Every scan is a full scan**; there is no incremental mode yet.
* **Duplicates are a heuristic** (same case-insensitive name + same size): false positives
  (different content) and false negatives (renamed copies) are possible.
* Statistics by year use UTC, whereas date filters use the server time zone (`TZ`): a file
  modified around midnight on 31 December may fall in a different year.
* Names that are not valid UTF-8 are shown with `\xNN` escapes.
* After changing the category mapping, `summary.yaml` is recomputed, but the history and the
  `category` column of the index files are only refreshed by the next scan.

**Scale**
* The **whole index lives in RAM** (≈ 260 bytes/file): about 2.7 GB for 10 M files.
* Index loading at startup takes about 5 s per million files; `/api/files` answers 503 meanwhile
  (the banner and statistics are served from `summary.yaml`).
* The treemap expands at most ~4,000 nodes per view; deeper levels are loaded when zooming.

**Interface**
* The UI and log messages are **in French only**.
* Name search is case-insensitive but **not accent-insensitive** (`ete` does not match `été`), and
  globs only support `*` and `?` (no `[...]` classes).
* Sorting by name uses code-point order of the lower-cased name, not a locale-aware order.
* CSV uses `;` as separator (French Excel default).
* Requires a recent browser (ES modules, CSS `:has()`, Canvas `roundRect`): Chrome/Edge 105+,
  Firefox 121+, Safari 16+.

**Security**
* A single administrator account, no roles, no SSO.
* No built-in HTTPS: use a reverse proxy for remote access.
* The brute-force counters are kept in memory and reset when the container restarts.
* With an empty `ADMIN_PASSWORD`, the first visitor creates the account: do it right after
  deploying, or do not expose the port beforehand.

---

## Roadmap

Ideas for future work, roughly by value for effort. Each item says *why* and gives a *how to*
starting point.

1. **Publish a ready-made image (GHCR, amd64 + arm64)**
   *Why:* removes steps 3–4 of the Dockge procedure; Dockge's **Update** button would then really
   pull new versions.
   *How to:* add `.github/workflows/docker.yml` using `docker/setup-qemu-action`,
   `docker/setup-buildx-action`, `docker/login-action` (registry `ghcr.io`, `GITHUB_TOKEN`) and
   `docker/build-push-action` with `platforms: linux/amd64,linux/arm64`, triggered on tags `v*`.
   Then replace `image: file-exploraviz:local` / `pull_policy: never` with
   `image: ghcr.io/bichoumac/file-exploraviz:latest` in the README.

2. **Continuous integration**
   *Why:* catch regressions on every push.
   *How to:* a GitHub Actions workflow running `pip install -r requirements-dev.txt && pytest` on
   `ubuntu-latest` (the POSIX-only tests then run too), plus `docker build` to check the image.

3. **English (and multilingual) user interface**
   *Why:* the UI is French only.
   *How to:* move every UI string from `web/assets/*.js` into `web/assets/i18n/fr.js` and `en.js`
   (plain objects), add a `t(key, params)` helper in `util.js`, pick the language from
   `navigator.language` with an override stored in `localStorage`, and translate category labels
   in `app/categories.py` on the client side (keep the ids). API error messages can return a
   `code` field alongside the French text so the client can translate them.

4. **Incremental scans**
   *Why:* full rescans of millions of files are slow on spinning disks.
   *How to:* store each folder's `mtime` in its index document (`app/scanner.py`,
   `IndexWriter.write_dir`). On the next scan, when a folder's `mtime` is unchanged, reuse its file
   list from the previous index instead of `stat()`-ing every file. Caveat: a folder `mtime` only
   changes when entries are added, removed or renamed, not when a file's content or size changes,
   so keep a periodic full scan (e.g. nightly) as an option in `ScanConfig`.

5. **Faster startup with a binary cache**
   *Why:* 5 s per million files to rebuild the index from YAML.
   *How to:* after building a `Store`, save its numpy columns with `numpy.savez` next to the YAML
   (e.g. `/cache/store_<root>.npz`, plus names and folder paths) with a format version and the YAML
   `footer` timestamp. At startup, load the `.npz` when it matches the YAML; otherwise fall back to
   `load_root_part`. YAML stays the source of truth.

6. **Disk usage vs apparent size**
   *Why:* on ZFS with compression, apparent sizes overstate real usage.
   *How to:* also record `st_blocks * 512` in the scanner (one more column in the index row and in
   `RootPart`/`Store`), then add a "size on disk" toggle in the Explorer, Statistics and Treemap.

7. **Real creation dates**
   *Why:* `st_ctime` is not the birth time on Linux.
   *How to:* call the `statx(2)` syscall through `ctypes` with `STATX_BTIME` in `scanner.py`
   (fall back to `st_ctime` when the filesystem does not report it), and label the column
   accordingly.

8. **Content-based duplicate confirmation**
   *Why:* the name + size heuristic produces false positives.
   *How to:* an optional, low-priority job that, for each candidate group from
   `aggregates._duplicates`, hashes the first and last 64 KB, then the full file (BLAKE2) only for
   remaining collisions; results cached in `/cache/hashes.yaml` keyed by `(path, size, mtime)`.
   The files are opened read-only, which is compatible with the `:ro` mounts.

9. **Alerts and monitoring**
   *Why:* be warned when a share grows fast or inaccessible items appear.
   *How to:* expose a Prometheus `/metrics` endpoint (totals per root and per category, scan
   duration) and/or a configurable webhook (Gotify, ntfy, Discord) fired at the end of a scan in
   `ScanManager._run` when thresholds from `config.yaml` are crossed.

10. **Accent-insensitive search and locale-aware sorting**
    *How to:* when building the search text in `Store.warm_up`, normalise names with
    `unicodedata.normalize("NFKD", …)` and strip combining marks (`unicodedata.combining`), and
    apply the same normalisation to `q`; for sorting, build the name rank from those normalised
    names.

11. **Multiple users and read-only roles, or SSO**
    *How to:* turn `AuthConfig` into a list of users with a `role` field (`admin` / `viewer`),
    check the role in a dependency used by mutating routes (`/api/scan`, `/api/config`,
    `/api/cache/clear`); or delegate to an OIDC provider (Authelia, Authentik) via the reverse
    proxy and `AUTH_DISABLED=true`.

12. **Per-root schedules**
    *How to:* add an optional `interval_minutes` to `RootConfig`, let `Scheduler.compute_next`
    pick the earliest due root, and call `ScanManager.start([root_id], "planifié")`.

13. **Frontend end-to-end tests**
    *How to:* Playwright tests (desktop and mobile viewports) against the app started on
    generated data (`scripts/gen_tree.py`), run in the CI workflow.

---

## Design decisions

Choices made outside, or on top of, the original specification:

1. **numpy** for the in-memory index: vectorised filters, sorts and aggregates. This is what keeps
   requests under 200 ms with 1 M files. Cost: about 48 MB in the image.
2. **Index rows written as JSON**, which is a subset of YAML: `CSafeLoader` read 200,000 rows in
   8.6 s, the line-by-line reader takes 0.4 s. The files remain valid YAML (checked by the tests);
   `CSafeLoader`/`CSafeDumper` are used for everything else and as a fallback.
3. **Home-made asyncio scheduler** instead of APScheduler: one dependency less and no useless
   wake-ups.
4. **Vanilla JavaScript** (ES modules) instead of Alpine.js. ECharts 5.6.0 is bundled in the image so
   it works offline.
5. **Scan thread** rather than a separate process: on Linux `nice` and `ioprio_set` apply per
   thread, which was verified (`NI 19`, `ionice idle` on that thread only).
6. **Privilege dropping in Python** (`app/entrypoint.py`) rather than `gosu`/`setpriv`: no system
   dependency, compatible with `read_only: true` and `cap_drop: ALL`.
7. **First scan at startup** when the cache is empty.
8. `ADMIN_*` and `SCAN_INTERVAL_MINUTES` are only **initial values**, so changes made in the UI are
   not overwritten on restart.
9. **CSV export** with `;` and a UTF-8 BOM so that French Excel opens it directly.
10. **Colours of the 11 categories**: palette checked for colour-vision deficiencies and contrast,
    in both light and dark themes. A category is always shown with an icon or a label as well.
11. Extra routes: `/api/auth/state`, `/api/auth/setup`, `/api/auth/password`, `/api/extensions`,
    `/api/cache/clear`.

## License

Project code: to be defined by the author. ECharts: Apache-2.0 (see `web/vendor/README.md`).
