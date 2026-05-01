"""Daily orchestrator. Run by GitHub Actions at 7am UK time.

Pipeline:
  1. Load configs (companies, roles, lookback rules)
  2. Detect first run (empty seen_jobs.json) and pick lookback window
  3. Scrape every company; record which were reachable
  4. Filter: by date, by location, hard exclusions
  5. Drop already-seen jobs (dedup safety net on top of the date filter)
  6. AI-score the survivors; keep loves_this + worth_a_look
  7. Pick today's quote
  8. Render email
  9. Send via Resend
 10. Update state files

Designed to fail soft: a single broken scraper or a single AI call failure
will not abort the run. The morning still ships.

Usage (locally):
  python main.py            - real run
  python main.py --dry      - skip the actual email send, write the rendered HTML to /tmp instead
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Loaded early so subsequent module-level reads of os.environ pick them up.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from lib import emailer, filter as filt, quotes, render, scorer, state
import scrapers

LOG = logging.getLogger("lucy_job_tracker")
REPO_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = REPO_ROOT / "config"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )


def run(dry: bool = False) -> int:
    _setup_logging()
    LOG.info("=== Daily run starting ===")

    companies_cfg = _load_json(CONFIG_DIR / "companies.json")["companies"]
    roles_cfg = _load_json(CONFIG_DIR / "roles.json")

    seen = state.load_seen()
    is_first_run = len(seen) == 0
    lookback_days = (
        roles_cfg["lookback"]["first_run_days"] if is_first_run
        else roles_cfg["lookback"]["ongoing_days"]
    )
    LOG.info("First run: %s. Lookback window: %d days.", is_first_run, lookback_days)

    # 1. Scrape ----------------------------------------------------------------
    raw_by_company = scrapers.fetch_all(companies_cfg)
    reached = [c["short"] for c in companies_cfg if raw_by_company.get(c["short"])]
    unreachable = [c["short"] for c in companies_cfg if not raw_by_company.get(c["short"])]
    LOG.info("Reached %d/%d companies", len(reached), len(companies_cfg))

    all_jobs: list[dict] = []
    for jobs in raw_by_company.values():
        all_jobs.extend(jobs)
    LOG.info("Total raw postings collected: %d", len(all_jobs))

    # 2. Date filter -----------------------------------------------------------
    all_jobs = filt.by_date(all_jobs, max_age_days=lookback_days)

    # 3. Location filter -------------------------------------------------------
    all_jobs = filt.by_location(
        all_jobs,
        include=roles_cfg["locations"]["include"],
        exclude=roles_cfg["locations"]["exclude"],
    )

    # 4. Hard exclusions -------------------------------------------------------
    survivors, excluded = filt.hard_exclusions(
        all_jobs,
        experience_patterns=roles_cfg["hard_exclusions"]["experience_required_phrases"],
        qualification_patterns=roles_cfg["hard_exclusions"]["qualification_required_phrases"],
    )

    # 5. Dedup against seen jobs ----------------------------------------------
    new_survivors = [j for j in survivors if state.is_new(j)]
    LOG.info("After dedup: %d new postings (was %d)", len(new_survivors), len(survivors))

    # 6. AI score --------------------------------------------------------------
    feedback = state.load_feedback()
    scored = scorer.score_all(
        new_survivors,
        target_roles=roles_cfg["target_roles"]["must_match_one"],
        fuzzy_categories=roles_cfg["target_roles"]["fuzzy_categories"],
        rejected_jobs=feedback.get("rejected_jobs", []),
    )
    matches = [j for j in scored if j.get("verdict") in ("loves_this", "worth_a_look")]
    matches.sort(key=lambda j: 0 if j["verdict"] == "loves_this" else 1)

    # 7. Quote ----------------------------------------------------------------
    quote = quotes.pick_today()

    # 8. Render ---------------------------------------------------------------
    subject, html = render.render_email(
        matches=matches,
        excluded=excluded,
        reached=reached,
        unreachable=unreachable,
        quote=quote,
        recipient_name=os.environ.get("LUCY_NAME", "Lucy"),
        recipient_email=os.environ.get("RECIPIENT_EMAIL", ""),
        is_first_run=is_first_run,
    )

    # 9. Send -----------------------------------------------------------------
    if dry:
        out_path = Path("/tmp/lucy_digest_preview.html")
        out_path.write_text(html, encoding="utf-8")
        LOG.info("DRY RUN. Wrote preview to %s", out_path)
    else:
        to_email = os.environ.get("LUCY_EMAIL")
        to_name = os.environ.get("LUCY_NAME", "Lucy")
        if not to_email:
            LOG.error("LUCY_EMAIL not set; refusing to send.")
            return 2
        emailer.send(subject=subject, html=html, to_email=to_email, to_name=to_name)

    # 10. Persist state -------------------------------------------------------
    # Mark every survivor (matched or not) as seen so we never re-score it.
    state.mark_seen(scored)

    LOG.info("=== Daily run complete: %d matches sent ===", len(matches))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Render but do not send")
    args = parser.parse_args()
    try:
        return run(dry=args.dry)
    except Exception as e:  # noqa: BLE001
        LOG.exception("Daily run failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
