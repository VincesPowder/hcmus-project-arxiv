"""Logging to console and to logs/<name>.log."""
import logging
from pathlib import Path


def setup_logger(name: str, log_dir="logs") -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (logging.FileHandler(Path(log_dir) / f"{name}.log", encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    return logging.getLogger(name)
