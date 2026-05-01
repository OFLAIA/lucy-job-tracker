"""SmartRecruiters adaptor.

SmartRecruiters exposes a public, CORS-enabled job-listings API:
  GET https://api.smartrecruiters.com/v1/companies/{slug}/postings
Returns paginated postings; each posting's detail can be fetched via:
  GET https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}

Used for: Beazley (slug 'beazley'). Possibly others if discovered later.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

LOG = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; LucyJobTracker/1.0)"
PAGE_LIMIT = 50
PAUSE_SECONDS = 0.5


def _list_url(slug: str, offset: int) -> str:
    return (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{slug}/postings?limit={PAGE_LIMIT}&offset={offset}"
    )


def _detail_url(slug: str, posting_id: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"


def _get(url: str, timeout: int = 20) -> dict[str, Any]:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _location_text(loc: dict[str, Any] | None) -> str:
    if not loc:
        return ""
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    return ", ".join([p for p in parts if p])


def _description_text(detail: dict[str, Any]) -> str:
    """SmartRecruiters splits description into sections; concatenate them."""
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        block = sections.get(key) or {}
        text = block.get("text") or ""
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def fetch_jobs(company: dict[str, Any]) -> list[dict[str, Any]]:
    slug = company["ats_config"]["company_slug"]
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        try:
            data = _get(_list_url(slug, offset))
        except requests.RequestException as e:
            LOG.warning("SmartRecruiters list failed for %s at offset %d: %s", slug, offset, e)
            break
        postings = data.get("content", [])
        if not postings:
            break
        for p in postings:
            posting_id = p.get("id")
            if not posting_id:
                continue
            try:
                detail = _get(_detail_url(slug, posting_id))
            except requests.RequestException as e:
                LOG.warning("SmartRecruiters detail failed for %s: %s", posting_id, e)
                detail = {}
            out.append({
                "company": company["name"],
                "title": p.get("name", "").strip(),
                "location": _location_text(p.get("location")),
                "url": p.get("ref") or p.get("postingUrl") or "",
                "description": _description_text(detail),
                "posted_on": p.get("releasedDate", ""),
                "external_id": p.get("refNumber") or posting_id,
                "raw": p,
            })
            time.sleep(PAUSE_SECONDS)
        offset += PAGE_LIMIT
        if offset >= data.get("totalFound", 0):
            break
    LOG.info("SmartRecruiters %s: %d postings fetched", company["short"], len(out))
    return out
