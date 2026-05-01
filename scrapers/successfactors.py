"""SuccessFactors / SAP careers fallback.

SuccessFactors deployments are inconsistent - some expose a JSON 'jobsearch'
endpoint, some require login, some only render via JavaScript. For SCOR and
Aegon UK we fall back to the same generic HTML scraper as 'custom' until we
verify each tenant's real shape.

This file exists so config/companies.json can declare adaptor='successfactors'
without erroring; under the hood it delegates to custom.py.
"""
from __future__ import annotations

from typing import Any

from . import custom


def fetch_jobs(company: dict[str, Any]) -> list[dict[str, Any]]:
    # TODO: once we have a sample of a real SuccessFactors response from
    # SCOR/Aegon, replace this with a proper JSON adaptor. For now, the
    # generic HTML scrape gives us a baseline.
    return custom.fetch_jobs(company)
