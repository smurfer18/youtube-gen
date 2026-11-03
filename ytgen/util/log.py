import logging
import sys
import time

_START = time.time()


class _Fmt(logging.Formatter):
    def format(self, record):
        el = time.time() - _START
        stage = getattr(record, "stage", record.name.split(".")[-1])
        return f"[{el:7.1f}s] {stage:<10} {record.getMessage()}"


def setup(verbose: bool = False) -> None:
    root = logging.getLogger("ytgen")
    if root.handlers:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(_Fmt())
    root.addHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"ytgen.{name}")
