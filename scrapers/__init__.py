"""Scraper dispatcher.

Each company's config declares an 'adaptor' name. This module routes to the
right adaptor module and exposes a single fetch_all() entry point.
"""
from __future__ import annotations

import logging
from typing import Any

from . import workday, smartrecruiters, successfactors, custom

LOG = logging.getLogger(__name__)

ADAPTORS = {
    "workday": workday,
    "smartrecruiters": smartrecruiters,
    "successfactors": successfactors,
    "custom": custom,
}


def fetch_for_company(company: dict[str, Any]) -> list[dict[str, Any]]:
    adaptor_name = company.get("adaptor")
    adaptor = ADAPTORS.get(adaptor_name)
    if adaptor is None:
        LOG.error("Unknown adaptor %r for %s; skipping", adaptor_name, company.get("short"))
        return []
    try:
        return adaptor.fetch_jobs(company)
    except Exception as e:  # noqa: BLE001 - never let one site break the run
        LOG.exception("Scraper crashed for %s: %s", company.get("short"), e)
        return []


def fetch_all(companies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return {company_short: [jobs]} for the whole list."""
    out: dict[str, list[dict[str, Any]]] = {}
    for c in companies:
        out[c["short"]] = fetch_for_company(c)
    return out
