"""Logs structurés au format logfmt : ``ts=... level=INFO logger=scan msg="..." cle=valeur``."""

from __future__ import annotations

import logging
import sys
import time

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName", "color_message"}


def _fmt_value(value: object) -> str:
    text = str(value)
    if text == "" or any(c in text for c in ' "=\n\t'):
        text = '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return text


class LogfmtFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
        parts = [
            f"ts={ts}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"msg={_fmt_value(record.getMessage())}",
        ]
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                parts.append(f"{key}={_fmt_value(value)}")
        if record.exc_info:
            parts.append(f"exc={_fmt_value(self.formatException(record.exc_info))}")
        return " ".join(parts)


def setup_logging(level: str = "INFO") -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(LogfmtFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Uvicorn : on garde ses messages d'erreur, mais pas un log par requête (sauf en DEBUG).
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = True
    access.setLevel(logging.DEBUG if root.level <= logging.DEBUG else logging.WARNING)
