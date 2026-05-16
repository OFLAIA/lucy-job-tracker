"""Industry catalog loading.

Catalogs live in companies/{industry}-{region}.json - one file per (industry, region)
combination. For v1 the only file is companies/insurance-uk.json.

Each catalog file has the shape:
  {
    "industry": "insurance",
    "region": "uk",
    "companies": [ {name, short, human_url, adaptor, ats_config, ...}, ... ]
  }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "companies"


def load(industry: str, region: str) -> list[dict[str, Any]]:
    """Return the list of companies for one (industry, region) catalog."""
    path = CATALOG_DIR / f"{industry}-{region}.json"
    if not path.exists():
        raise FileNotFoundError(f"No catalog at {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("companies", [])


def all_company_keys() -> list[tuple[str, str]]:
    """Return every (industry, region) we have a catalog for. Useful for the
    'scrape everything we know about' step in the daily workflow."""
    if not CATALOG_DIR.exists():
        return []
    keys = []
    for f in sorted(CATALOG_DIR.glob("*.json")):
        try:
            industry, region = f.stem.rsplit("-", 1)
            keys.append((industry, region))
        except ValueError:
            LOG.warning("Catalog file %s does not match {industry}-{region}.json", f.name)
    return keys
