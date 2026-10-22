"""Planification des scans automatiques (boucle asyncio, aucun réveil inutile)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from .config import ConfigManager, OffPeak
from .scan_manager import ScanManager
from .timeutil import parse_iso

log = logging.getLogger("scheduler")

STARTUP_DELAY_S = 10


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_offpeak(ts: float, op: OffPeak) -> bool:
    dt = datetime.fromtimestamp(ts)
    now, start, end = dt.hour * 60 + dt.minute, _minutes(op.start), _minutes(op.end)
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end  # la plage traverse minuit


def next_offpeak(ts: float, op: OffPeak) -> float:
    """Premier instant >= ts situé dans la plage d'heures creuses."""
    if in_offpeak(ts, op):
        return ts
    dt = datetime.fromtimestamp(ts)
    start = _minutes(op.start)
    candidate = dt.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    if candidate.timestamp() <= ts:
        candidate += timedelta(days=1)
    return candidate.timestamp()


class Scheduler:
    def __init__(self, cfg: ConfigManager, scans: ScanManager, initial_scan: bool = True):
        self.cfg = cfg
        self.scans = scans
        self.initial_scan = initial_scan
        self.started_ts = time.time()
        self.next_run: float | None = None
        self._skip_until = 0.0
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        scans.listeners.append(self.wake)

    def compute_next(self, now: float) -> float | None:
        scan = self.cfg.config.scan
        if scan.interval_minutes <= 0 or self.scans.running:
            return None
        # Dernier scan terminé, annulé ou en erreur : une annulation ne relance pas aussitôt.
        attempts = [self.scans.last_finished_ts(), parse_iso(self.scans.info.get("finished_at"))]
        last = max((t for t in attempts if t is not None), default=None)
        if last is None:
            if not self.initial_scan:
                base = self.started_ts + scan.interval_minutes * 60
            else:
                base = self.started_ts + STARTUP_DELAY_S
        else:
            base = last + scan.interval_minutes * 60
        base = max(base, self.started_ts + STARTUP_DELAY_S, self._skip_until, now)
        if scan.offpeak.enabled:
            base = next_offpeak(base, scan.offpeak)
        return base

    def wake(self) -> None:
        """Recalcule l'échéance (fin de scan, changement de configuration). Thread-safe."""
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while True:
            now = time.time()
            self.next_run = self.compute_next(now)
            timeout = None if self.next_run is None else max(0.0, self.next_run - now)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
                self._wake.clear()
                continue
            except TimeoutError:
                pass
            started, message = self.scans.start(None, "planifié")
            log.info("scan planifié", extra={"started": started, "detail": message})
            if not started:
                if not self.scans.state.index_ready.is_set():
                    self._skip_until = time.time() + 15  # index encore en chargement
                else:
                    # Rien à scanner (aucune racine) : on retente à l'intervalle suivant.
                    self._skip_until = time.time() + max(60, self.cfg.config.scan.interval_minutes * 60)
