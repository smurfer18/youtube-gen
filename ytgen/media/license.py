"""License classification for every asset. Conservative by design: anything
not clearly allowing commercial reuse and modification is 'uncertain' (flagged,
not used unless explicitly allowed) or 'rejected'."""

from __future__ import annotations

import re

RULES = [
    # (pattern, status, attribution_required, commercial_ok)
    (r"\b(nc|non[- ]?commercial)\b", "rejected", True, False),
    (r"\b(nd|no[- ]?deriv)", "rejected", True, False),
    (r"fair use|non-free|all rights reserved|copyright(ed)?\s*(?!free)", "rejected", True, False),
    (r"\bcc0\b|publicdomain/zero", "cc0", False, True),
    (r"public domain|\bpd\b|publicdomain/mark|pd-|no known (copyright )?restrictions|nasa", "public_domain", False, True),
    (r"by[- ]sa|share[- ]?alike", "share_alike", True, True),
    (r"\bcc[- ]by\b|creativecommons\.org/licenses/by/|attribution", "attribution", True, True),
    (r"pexels license|pixabay content license|unsplash license", "platform", False, True),
    (r"owned|licensed by creator", "owned", False, True),
    (r"ai[- ]generated", "ai_generated", False, True),
    (r"copyrighted free use", "public_domain", False, True),
]


def classify(license_text: str) -> tuple[str, bool, bool]:
    t = (license_text or "").lower().strip()
    if not t:
        return "uncertain", True, False
    for pat, status, attr, com in RULES:
        if re.search(pat, t):
            return status, attr, com
    return "uncertain", True, False


def apply(asset) -> None:
    status, attr, com = classify(asset.license)
    asset.license_status, asset.attribution_required, asset.commercial_ok = status, attr, com


def usable(asset, allow_uncertain: bool = False) -> bool:
    if not asset.license_status:
        apply(asset)
    if asset.license_status == "rejected":
        return False
    if asset.license_status == "uncertain":
        return allow_uncertain
    return asset.commercial_ok
