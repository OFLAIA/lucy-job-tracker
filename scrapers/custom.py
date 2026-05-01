"""Best-effort scraper for companies without a known ATS.

Strategy: GET the human careers URL, parse with BeautifulSoup, look for a
list of links whose anchor text resembles a job title and whose href looks
like a job-detail page. This is fragile by definition - the daily run logs
when this adaptor returns zero results so we know to investigate.

Used for: Howden, Asta. Both will likely need bespoke selectors once we
see their actual page structure on the first real run; this generic version
gets us off the ground.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; LucyJobTracker/1.0)"

# Anchor text patterns that look like job titles for our domain.
TITLE_HINTS = re.compile(
    r"\b("
    r"underwriter|underwriting|broker|broking|account handler|account executive|"
    r"claims|trainee|apprentice|graduate|junior|assistant|operations"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_job_link(anchor) -> bool:
    text = (anchor.get_text() or "").strip()
    href = anchor.get("href") or ""
    if not text or not href or href.startswith("mailto:"):
        return False
    if not TITLE_HINTS.search(text):
        return False
    # Plausible job-detail URLs usually contain "job", "career", or numeric ids.
    return bool(re.search(r"(/job|/careers?/|/vacancy|\d{3,})", href, re.IGNORECASE))


def fetch_jobs(company: dict[str, Any]) -> list[dict[str, Any]]:
    url = company["human_url"]
    out: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        LOG.warning("Custom scrape failed for %s (%s): %s", company["short"], url, e)
        return out
    soup = BeautifulSoup(resp.text, "lxml")
    seen_urls = set()
    for a in soup.find_all("a"):
        if not _looks_like_job_link(a):
            continue
        full_url = urljoin(url, a["href"])
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        out.append({
            "company": company["name"],
            "title": a.get_text(strip=True),
            # Custom sites rarely expose location in the listing - leave blank
            # and let the AI scorer infer from the description we'll fetch later.
            "location": "",
            "url": full_url,
            # Don't fetch detail here - too risky on JS-rendered sites. Let the
            # scorer see title-only and conservatively flag for review.
            "description": "",
            "posted_on": "",
            "external_id": None,
            "raw": {"source": "custom_html"},
        })
    LOG.info("Custom %s: %d candidate links found", company["short"], len(out))
    return out
