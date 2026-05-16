# Job Tracker

A self-serve daily job digest for friends and family. Users sign up via a small web form, pick their industry/region/seniority/locations, and receive a warm morning email with the live entry-level (or whatever-level) roles that fit, scored by AI.

Currently covers one industry-region catalog: **Insurance, UK** (32 companies — brokers, composite insurers, Lloyd's syndicates, reinsurers).

## How it works

```
SHARED SCRAPE (once per day, 06:00 UTC via GitHub Actions)
  -> hit every company in companies/insurance-uk.json
  -> cache raw postings in state/scrape_cache/

PER-USER LOOP (for each active user in users/*.json)
  -> read from shared cache
  -> apply user's date + location filters
  -> dedup against their seen_jobs
  -> AI-score against their seniority + role focus
  -> pick their motivational quote (per-user tone weights)
  -> render and send their email
  -> commit updated state back to repo
```

The AI scoring is the single source of truth for relevance. There are no regex blacklists — the AI judges each posting against the user's seniority bracket and role focus.

## Architecture

```
job-tracker/
├── companies/
│   └── insurance-uk.json         # 32 companies + their careers ATS
├── users/
│   └── lucy.json                 # one file per user (config)
├── state/
│   ├── scrape_cache/             # shared per-company cache
│   └── users/{user_id}/          # per-user dedup + feedback + quote history
├── lib/
│   ├── catalog.py                # load company catalogs
│   ├── user.py                   # load + validate user configs
│   ├── state.py                  # per-user state, shared scrape cache
│   ├── scorer.py                 # AI relevance (parameterised per user)
│   ├── filter.py                 # date + location filters
│   ├── quotes.py                 # per-user quote picker
│   ├── render.py                 # email rendering
│   └── emailer.py                # Resend send
├── scrapers/                     # adaptors per ATS (Workday/SmartRecruiters/etc.)
├── templates/email.html          # email template
├── docs/index.html               # GitHub Pages signup form
├── scripts/add_user.sh           # interactive helper to add a user
├── .github/workflows/daily.yml   # daily cron
├── main.py                       # orchestrator
└── requirements.txt
```

## One-time setup (for Ian)

This is everything you need to do once. After this, adding a new user is one paste away.

### 1. Set up Formspree (for the signup form)

Go to [formspree.io](https://formspree.io), sign up for a free account, and create a new form (any project name).

Copy the form's endpoint URL — it looks like `https://formspree.io/f/xyzabc123`.

Open `docs/index.html` in this repo, find the line `action="YOUR_FORMSPREE_ENDPOINT"`, and replace `YOUR_FORMSPREE_ENDPOINT` with that URL.

Commit and push:

```bash
git add docs/index.html && git commit -m "Wire up Formspree endpoint" && git push
```

Formspree will email you (`ioflaherty65@googlemail.com`) every time someone submits the form.

### 2. Enable GitHub Pages

On the repo page, go to **Settings → Pages**.

Under "Build and deployment":
- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**
- Save

After a minute, the signup form is live at `https://OFLAIA.github.io/lucy-job-tracker/`. Share that URL with anyone you want to onboard.

### 3. Everything else is already set up from the v1 work

GitHub Actions secrets (`RESEND_API_KEY`, `ANTHROPIC_API_KEY`, `RECIPIENT_EMAIL`) are already in place from when you set up Lucy's tracker. The cron is already running daily. You're good.

## Adding a new user

When someone submits the signup form, Formspree emails you the submission. Then:

```bash
cd path/to/lucy_job_tracker
bash scripts/add_user.sh
```

It'll prompt you for each field. Paste them from the Formspree email. The script:

1. Creates `users/{first-name}.json` with the right shape
2. Validates the config
3. Commits, pulls, pushes
4. Optionally triggers a workflow run so they get their first email today

Their first email will be a 14-day backfill of currently-live roles. Every morning after that they get the new openings from the last day or so.

## Pausing or stopping a user

For v1 this is manual. Open `users/{name}.json` and either:

- Set `"active": false` to stop emails entirely, or
- Set `"paused_until": "2026-06-15"` to pause until that date (resumes automatically)

Commit and push. Next morning's run will skip them.

## When Lucy (or anyone) clicks "Not a match"

Each job card has a "Not a match" button. It's a `mailto:` link that lands a reply in your inbox with a structured subject:

```
[job-tracker] Not a match: a3f8e9d2c4b1
```

For v1 this is manual. Open `state/users/{user_id}/feedback.json` and append to `rejected_jobs`:

```json
{ "id": "a3f8e9d2c4b1", "title": "Job title", "company": "Company", "reason": "why not" }
```

Commit and push. Next morning's run feeds the last 8 rejections into the AI prompt so it learns the pattern.

(A v2 nice-to-have: wire up Resend's inbound webhook to automate this. Not needed at friends-and-family scale.)

## Adding a new industry or region

To add (for example) Insurance US:

1. Create `companies/insurance-us.json` with the same shape as the UK one.
2. Update `docs/index.html` to expose **United States** as a region option (and adjust the locations multi-select).

The pipeline picks up `companies/*.json` automatically — no code change needed.

## What changed from v1 (Lucy's single-user setup)

- `config/companies.json` → `companies/insurance-uk.json` (catalog grew from 12 → 32)
- `config/roles.json` → per-user role focus in `users/{id}.json` (no global roles config)
- `state/*.json` → `state/users/{id}/*.json` (per-user) and `state/scrape_cache/` (shared)
- `main.py` rewritten: scrape once, loop over active users
- AI scorer prompt now takes name, industry, seniority, locations, role_focus as parameters
- Email subject line generic ("N new insurance roles today" → "N new roles today") — though for v1 we keep the "insurance" hardcode since there's only one industry
- Set Aside section removed entirely (AI is the only judge; "skip" means dropped silently)
- GitHub Pages signup form added at `docs/index.html`

Lucy's existing seen_jobs and feedback are preserved at `state/users/lucy/`.

## Local development

```bash
pip install -r requirements.txt

# Full real run (will scrape and send emails)
python main.py

# Render but don't send - preview HTML written to /tmp/digest_preview_{user_id}.html
python main.py --dry

# Process just one user
python main.py --only-user lucy --dry

# Skip the scrape, reuse existing cache (fast iteration)
python main.py --only-user lucy --skip-scrape --dry
```

## Troubleshooting

**Action ran but nobody got an email.**
Check the Action logs for errors. Most likely cause: `RESEND_API_KEY` or `ANTHROPIC_API_KEY` not set, or a recipient email is malformed in a user config.

**Same company shows "Couldn't reach" every day.**
The scraper for that company needs work. Open `companies/insurance-uk.json`, find the entry, try a different `adaptor` value (`workday` / `smartrecruiters` / `successfactors` / `custom`). For Workday, verify the `tenant` and `wd_domain` match the careers page URL.

**A user wants a location we don't expose in the signup form.**
Edit `docs/index.html` to add the option. Existing users can also be edited directly in `users/{id}.json` to add custom locations.

**The cron isn't firing.**
GitHub disables scheduled workflows on inactive repos (no commits for 60 days). The state-commit step on every successful run keeps the repo "active" — so this only happens if the workflow has been broken for 2 months. Reactivate by manually triggering once from the Actions tab.

**Cost going up?**
Anthropic API cost scales with number of unique postings × number of users. Check `state/scrape_cache/*.json` — if some companies are returning hundreds of postings (because the date filter is letting too many through), tighten the lookback in `main.py` (currently 14 days first run, 2 days ongoing).

## What's not in this version

- Automatic processing of feedback replies (currently manual).
- A user dashboard for self-service config changes (currently you edit `users/{id}.json`).
- More industries — only Insurance UK is wired up.
- A real sender domain — uses `onboarding@resend.dev` so users need to whitelist it once.
- Per-user send times in different timezones — everyone gets 06:00 UTC = 07:00 UK in BST.

All reasonable v2 additions. None blocking for friends-and-family use.
