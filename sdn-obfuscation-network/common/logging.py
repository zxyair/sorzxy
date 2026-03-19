from __future__ import annotations

import logging
import os


def setup_logging(component: str, level: str | None = None) -> logging.Logger:
    lvl = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger(component)

