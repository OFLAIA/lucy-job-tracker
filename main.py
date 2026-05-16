"""Multi-user daily orchestrator.

Pipeline:
  Phase 1: SHARED SCRAPE (runs once per day)
    - For each catalog (industry+region we cover):
      - For each company in the catalog:
        - Scrape it
        - Save raw results to state/scrape_cache/{company_short}.json

  Phase 2: PER-USER PROCESSING (runs once per active user per day)
    - Load user's config
    - For each company in the user's industry+region catalog:
      - Read the cached scrape results
    - Apply date + location filters
    - Dedup against the user's seen_jobs
    - AI-score against the user's seniority + role focus
    - Pick the user's quote (per-user history + tone weights)
    - Render the email (per-user greeting + content)
    - Send via Resend
    - Update the user's seen_jobs

Designed to fail soft:
  - A broken scraper for one company doesn't abort the whole scrape phase.
  - A failure for one user doesn't stop the rest from getting their email.

Usage:
  python main.py                       - normal: scrape + process all active users
  python main.py --dry                 - scrape + process, write preview HTML to /tmp instead of sending
  python main.py --only-user lucy      - process only the named user (still does the scrape)
  python main.py --skip-scrape         - skip the scrape phase, use existing cache (fast dev iteration)
"""
from __future__ import annotations

import argparse
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

from lib import catalog, emailer, filter as filt, quotes, render, scorer, state, user as user_mod
import scrapers

LOG = logging.getLogger("job_tracker")
REPO_ROOT = Path(__file__).resolve().parent


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )


# --- Phase 1: shared scrape -------------------------------------------------

def scrape_all_catalogs() -> dict[tuple[str, str], dict[str, list[dict]]]:
    """Scrape every company in every catalog we have. Returns {(industry, region): {short: jobs}}.

    Also writes each company's results to state/scrape_cache/ for downstream use.
    """
    results: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for industry, region in catalog.all_company_keys():
        LOG.info("=== Scraping catalog: %s / %s ===", industry, region)
        companies = catalog.load(industry, region)
        per_company = scrapers.fetch_all(companies)
        for short, jobs in per_company.items():
            state.save_scrape_cache(short, jobs)
        results[(industry, region)] = per_company
    return results


def load_cached_scrape(industry: str, region: str) -> tuple[dict[str, list[dict]], list[str], list[str]]:
    """Read the latest cached scrape results for one catalog.

    Returns (jobs_by_short, reached_shorts, unreachable_shorts).
    """
    companies = catalog.load(industry, region)
    jobs_by_short: dict[str, list[dict]] = {}
    reached = []
    unreachable = []
    for c in companies:
        short = c["short"]
        cached = state.load_scrape_cache(short)
        jobs_by_short[short] = cached
        if cached:
            reached.append(short)
        else:
            unreachable.append(short)
    return jobs_by_short, reached, unreachable


# --- Phase 2: per-user processing -------------------------------------------

def process_user(user_cfg: dict, dry: bool = False) -> int:
    """Run the full pipeline for one user. Returns 0 on success, non-zero on failure."""
    user_id = user_cfg["id"]
    LOG.info("=== Processing user: %s ===", user_id)

    errors = user_mod.validate(user_cfg)
    if errors:
        LOG.error("[%s] invalid user config: %s", user_id, "; ".join(errors))
        return 2

    industry = user_cfg["industry"]
    region = user_cfg["region"]

    # 1. Pull from shared cache
    jobs_by_short, reached, unreachable = load_cached_scrape(industry, region)

    # Optional per-user company selection. If 'companies' is missing or empty,
    # treat as 'monitor all' (the default for users who don't customise).
    user_companies = user_cfg.get("companies")
    if user_companies:
        before = len(jobs_by_short)
        jobs_by_short = {s: js for s, js in jobs_by_short.items() if s in user_companies}
        reached = [s for s in reached if s in user_companies]
        unreachable = [s for s in unreachable if s in user_companies]
        LOG.info("[%s] company filter: %d/%d companies selected",
                 user_id, len(jobs_by_short), before)

    all_jobs = [j for jobs in jobs_by_short.values() for j in jobs]
    LOG.info("[%s] cache has %d raw postings from %d/%d companies",
             user_id, len(all_jobs), len(reached), len(reached) + len(unreachable))

    # 2. First-run detection: empty seen_jobs => widen the lookback
    seen = state.load_seen(user_id)
    is_first_run = len(seen) == 0
    lookback_days = 14 if is_first_run else 2
    LOG.info("[%s] first_run=%s lookback=%d days", user_id, is_first_run, lookback_days)

    # 3. Date filter
    all_jobs = filt.by_date(all_jobs, max_age_days=lookback_days)

    # 4. Location filter (using the user's locations as the include list)
    all_jobs = filt.by_location(
        all_jobs,
        include=user_cfg["locations"],
        exclude=user_cfg.get("exclude_locations", []),
    )

    # 5. Dedup against this user's seen_jobs
    new_survivors = [j for j in all_jobs if state.is_new_for(user_id, j, seen_index=seen)]
    LOG.info("[%s] after dedup: %d new postings (was %d)", user_id, len(new_survivors), len(all_jobs))

    # 6. AI score
    feedback = state.load_feedback(user_id)
    scored = scorer.score_all(
        new_survivors,
        user_cfg=user_cfg,
        rejected_jobs=feedback.get("rejected_jobs", []),
    )
    matches = [j for j in scored if j.get("verdict") in ("loves_this", "worth_a_look")]
    matches.sort(key=lambda j: 0 if j["verdict"] == "loves_this" else 1)

    # 7. Quote
    quote = quotes.pick_today(user_id)

    # 8. Render
    subject, html = render.render_email(
        matches=matches,
        considered_count=len(scored),
        reached=reached,
        unreachable=unreachable,
        quote=quote,
        recipient_name=user_cfg["name"],
        recipient_email=os.environ.get("RECIPIENT_EMAIL", ""),  # admin email, for feedback replies
        is_first_run=is_first_run,
    )

    # 9. Send
    if dry:
        out_path = Path(f"/tmp/digest_preview_{user_id}.html")
        out_path.write_text(html, encoding="utf-8")
        LOG.info("[%s] DRY RUN. Wrote preview to %s", user_id, out_path)
    else:
        try:
            emailer.send(subject=subject, html=html, to_email=user_cfg["email"], to_name=user_cfg["name"])
        except Exception as e:  # noqa: BLE001
            LOG.exception("[%s] email send failed: %s", user_id, e)
            return 3

    # 10. Persist state
    state.mark_seen(user_id, scored)
    LOG.info("[%s] done. %d matches sent.", user_id, len(matches))
    return 0


# --- driver -----------------------------------------------------------------

def run(dry: bool = False, only_user: str | None = None, skip_scrape: bool = False) -> int:
    _setup_logging()
    LOG.info("=== Daily run starting (dry=%s, only_user=%s, skip_scrape=%s) ===",
             dry, only_user, skip_scrape)

    if not skip_scrape:
        scrape_all_catalogs()

    users = user_mod.active_users()
    if only_user:
        users = [u for u in users if u.get("id") == only_user]
        if not users:
            LOG.error("No active user with id=%s", only_user)
            return 4

    LOG.info("Processing %d active user(s)", len(users))
    exit_code = 0
    for u in users:
        try:
            rc = process_user(u, dry=dry)
            if rc != 0:
                exit_code = rc
        except Exception as e:  # noqa: BLE001
            LOG.exception("Unhandled error for user %s: %s", u.get("id"), e)
            exit_code = 5
    LOG.info("=== Daily run complete (exit=%d) ===", exit_code)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Render but do not send emails")
    parser.add_argument("--only-user", help="Process only this user id")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip the scrape phase; use cached results")
    args = parser.parse_args()
    try:
        return run(dry=args.dry, only_user=args.only_user, skip_scrape=args.skip_scrape)
    except Exception as e:  # noqa: BLE001
        LOG.exception("Top-level run failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
