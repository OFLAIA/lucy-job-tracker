"""Workday adaptor.

Workday tenants expose a JSON 'cxs' endpoint that returns paginated job
listings. The shape is consistent across tenants, so one adaptor handles
WTW, Gallagher, Marsh, Aviva, Chubb, AON, and AIG (Talbot's parent).

Endpoint:
  POST https://{tenant}.{wd_domain}/wday/cxs/{tenant}/{site}/jobs
Body:
  {"appliedFacets": {...}, "limit": 20, "offset": 0, "searchText": ""}

Response sketch:
  {
    "jobPostings": [
      {"title": "...", "externalPath": "/job/London/...", "locationsText": "London",
       "postedOn": "Posted Today", "bulletFields": ["RXXXX"]}
    ],
    "total": 134
  }

Each posting's full description lives at the externalPath URL appended to
the site root, also fetchable as JSON via the cxs job-detail endpoint.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

LOG = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; LucyJobTracker/1.0; +github.com/ian/lucy-job-tracker)"
PAGE_LIMIT = 20
MAX_PAGES = 25  # 500 listings is plenty for 12 mid-to-large insurers
PAUSE_SECONDS = 1.0


def _site_root(cfg: dict[str, str]) -> str:
    return f"https://{cfg['tenant']}.{cfg['wd_domain']}/{cfg['site']}"


def _cxs_jobs_url(cfg: dict[str, str]) -> str:
    return (
        f"https://{cfg['tenant']}.{cfg['wd_domain']}"
        f"/wday/cxs/{cfg['tenant']}/{cfg['site']}/jobs"
    )


def _cxs_detail_url(cfg: dict[str, str], external_path: str) -> str:
    # externalPath looks like "/job/London/Underwriting-Apprentice_R1234"
    return (
        f"https://{cfg['tenant']}.{cfg['wd_domain']}"
        f"/wday/cxs/{cfg['tenant']}/{cfg['site']}{external_path}"
    )


def _public_url(cfg: dict[str, str], external_path: str) -> str:
    return f"{_site_root(cfg)}{external_path}"


def _post_json(url: str, body: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get_json(url: str, timeout: int = 20) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _list_postings(cfg: dict[str, str]) -> Iterator[dict[str, Any]]:
    """Yield raw posting summaries (title, externalPath, locationsText, ...)."""
    url = _cxs_jobs_url(cfg)
    offset = 0
    for _ in range(MAX_PAGES):
        body = {"appliedFacets": {}, "limit": PAGE_LIMIT, "offset": offset, "searchText": ""}
        try:
            data = _post_json(url, body)
        except requests.RequestException as e:
            LOG.warning("Workday list failed for %s at offset %d: %s", cfg.get("tenant"), offset, e)
            return
        postings = data.get("jobPostings", [])
        if not postings:
            return
        for p in postings:
            yield p
        offset += PAGE_LIMIT
        if offset >= data.get("total", 0):
            return
        time.sleep(PAUSE_SECONDS)


def _fetch_detail(cfg: dict[str, str], external_path: str) -> dict[str, Any]:
    """Fetch full job description JSON for one posting."""
    try:
        return _get_json(_cxs_detail_url(cfg, external_path))
    except requests.RequestException as e:
        LOG.warning("Workday detail failed for %s: %s", external_path, e)
        return {}


def fetch_jobs(company: dict[str, Any]) -> list[dict[str, Any]]:
    """Public entry point. Returns a list of normalised job dicts:
        {company, title, location, url, description, posted_on, raw}
    """
    cfg = company["ats_config"]
    out: list[dict[str, Any]] = []
    for p in _list_postings(cfg):
        external_path = p.get("externalPath", "")
        if not external_path:
            continue
        public_url = _public_url(cfg, external_path)
        # Fetch full description so we can score and detect experience requirements.
        # If detail fetch fails, fall back to title-only and let downstream filters
        # handle it conservatively.
        detail = _fetch_detail(cfg, external_path)
        job_posting_info = detail.get("jobPostingInfo", {})
        description = job_posting_info.get("jobDescription", "") or ""
        out.append({
            "company": company["name"],
            "title": p.get("title", "").strip(),
            "location": p.get("locationsText", "").strip(),
            "url": public_url,
            "description": description,
            "posted_on": p.get("postedOn", "").strip(),
            "external_id": (p.get("bulletFields") or [None])[0],
            "raw": p,
        })
        time.sleep(PAUSE_SECONDS)
    LOG.info("Workday %s: %d postings fetched", company["short"], len(out))
    return out
