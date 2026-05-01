# Lucy's Job Tracker

A small, private system that checks 12 insurance careers pages every morning, filters for genuinely entry-level UK roles, and emails Lucy a short, warm digest at 7am UK time.

It also includes a motivational quote each morning with thumbs up/down feedback so the tone gets better over time.

## How it works

```
GitHub Actions cron (06:00 UTC = 07:00 UK in BST)
    -> python main.py
        1. Scrape 12 careers pages
        2. Filter by date (14 days first run, ~2 days after)
        3. Filter by location (London / Essex / hybrid / remote-UK)
        4. Drop anything requiring prior experience or a degree
        5. Drop anything we have already shown Lucy
        6. AI score the survivors -> "loves this" / "worth a look" / skip
        7. Pick today's quote (avoids repeats, learns from her thumbs)
        8. Render the email
        9. Send via Resend
       10. Commit updated state files back to the repo
```

State (which jobs we've already shown her, which quotes we've already used, which roles she's flagged "not a match") lives in `state/*.json` and is committed back to the repo at the end of every run, so memory persists even though GitHub Actions runners are stateless.

## What you need

- A GitHub account (you already have one).
- The Resend API key (already saved in `.env` locally).
- The Anthropic API key (already saved in `.env` locally).
- About 10 minutes for the one-time setup below.

Lucy does **not** need a GitHub account. She just receives email.

## One-time setup

### 1. Create a private repo

On github.com, create a new **private** repository called `lucy-job-tracker` (or whatever you like). Don't add a README, .gitignore, or licence on the GitHub side - we'll push everything from here.

### 2. Push this folder to it

From inside this `lucy_job_tracker` folder, in a terminal:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin git@github.com:YOUR-USERNAME/lucy-job-tracker.git
git push -u origin main
```

Note: `.env` is gitignored, so your API keys are **not** uploaded. We add them to GitHub separately as secrets.

### 3. Add the secrets

On the repo page, go to **Settings -> Secrets and variables -> Actions -> New repository secret** and add each of these:

| Secret name | Value |
|---|---|
| `RESEND_API_KEY` | `re_U7VCDbRr_...` (from your `.env`) |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` (from your `.env`) |
| `LUCY_EMAIL` | `lucysitunes1@gmail.com` |
| `LUCY_NAME` | `Lucy` |
| `RECIPIENT_EMAIL` | `ioflaherty65@googlemail.com` |
| `RECIPIENT_NAME` | `Ian` |

`RECIPIENT_EMAIL` is where her "Not a match" and quote-feedback replies will go - your inbox - so the system can learn from them.

### 4. Enable Actions

GitHub usually enables Actions automatically on a new repo, but if you see a banner asking you to confirm on the **Actions** tab, click confirm.

### 5. Trigger the first run manually

Go to the **Actions** tab -> **Daily job digest** in the left sidebar -> **Run workflow** -> **Run workflow** (green button).

Watch the run. The first time you should see a 14-day backfill - Lucy gets a digest of everything live in the last fortnight. After that, she only sees postings from the last day or so.

If the run goes green and Lucy gets the email, you're done.

### 6. Whitelist the sender in Lucy's Gmail (one-off)

Emails come from `onboarding@resend.dev` (the free Resend default sender). Gmail occasionally puts those in Promotions or Spam on the first delivery.

Ask Lucy to:

1. Check Promotions, Updates, and Spam for the first email.
2. Open it, click the three-dot menu, **Move to Inbox**.
3. Click the sender name -> **Add to contacts**.
4. Optionally, in Gmail Settings -> Filters and Blocked Addresses -> Create a new filter, "From: onboarding@resend.dev" -> Never send to spam, always mark as important.

After that, every morning's digest will land cleanly in her inbox.

## Day-to-day

The job runs itself at 06:00 UTC every day. You don't need to do anything.

### When Lucy clicks "Not a match"

Each job card in the email has a "Not a match" button. It's a `mailto:` link that opens her email composer with a structured subject line like:

```
[lucy-job-tracker] Not a match: a3f8e9d2c4b1
```

For now, those replies arrive in **your** inbox (`ioflaherty65@googlemail.com`). When you see one, drop the job id (the bit after the colon) into `state/feedback.json` under `rejected_jobs[].id` along with a short reason, commit, and push. Next morning's run will use that as a negative example for the AI scorer.

A future v2 can wire this up to Resend's inbound webhook so it happens automatically. Not needed for v1.

### When Lucy gives a quote thumbs up or down

Same idea - `mailto:` link, structured subject, lands in your inbox. The id is in the subject. Open `state/feedback.json` -> `quote_thumbs[]` and append:

```json
{ "id": "rooseveltdoonething", "verdict": "up" }
```

Or just nudge the `tone_weights` directly - e.g. if Lucy keeps thumbing up the "gentle" quotes and thumbing down "sharp", bump `gentle` to `2.0` and drop `sharp` to `0.5`. The picker re-weights randomly each morning from those values.

### If a careers page changes shape

The 12 scrapers are educated guesses based on which platforms each company appears to use. If one starts returning zero jobs day after day while the others find postings, the company has probably moved platforms.

Open `config/companies.json`, find the offending entry, and:

- Try a different `adaptor` (`workday`, `smartrecruiters`, `successfactors`, or `custom`).
- For workday, the `tenant`, `wd_domain`, and `site` fields need to match what's in the careers page URL.
- For smartrecruiters, the `slug` matches the company's URL on jobs.smartrecruiters.com.
- The `custom` adaptor is the fallback - it scrapes anchors that look like job titles.

Bump the `verified: false` flag to `true` once you've confirmed it works.

## Running locally (optional)

If you want to test changes without burning a real email:

```bash
pip install -r requirements.txt
python main.py --dry
```

`--dry` skips the Resend send and writes the rendered email to `/tmp/lucy_digest_preview.html` instead. Open that in your browser to see what would have been sent.

## File map

```
lucy_job_tracker/
├── .github/workflows/daily.yml   GitHub Actions cron
├── config/
│   ├── companies.json            12 companies + their careers system
│   ├── roles.json                Target roles, locations, hard exclusions
│   └── quotes.json               125 motivational quotes
├── lib/
│   ├── state.py                  Persists seen jobs, feedback, quote history
│   ├── filter.py                 Date / location / hard-exclusion filters
│   ├── scorer.py                 AI relevance scoring (Anthropic)
│   ├── quotes.py                 Tone-weighted quote picker
│   ├── render.py                 Email rendering (Jinja2)
│   └── emailer.py                Resend send
├── scrapers/
│   ├── workday.py                Workday cxs JSON API adaptor
│   ├── smartrecruiters.py        SmartRecruiters public API adaptor
│   ├── successfactors.py         SuccessFactors HTML adaptor
│   └── custom.py                 BeautifulSoup fallback
├── templates/email.html          Email template (table-based, Gmail-safe)
├── state/                        Memory between runs (committed back)
├── main.py                       Orchestrator
├── requirements.txt
└── .env                          Local dev secrets (gitignored)
```

## Troubleshooting

**The Action ran but Lucy didn't get an email.**
Check the Action logs. If it says "RESEND_API_KEY not set" or similar, the secret isn't named exactly right - re-add it. If it ran cleanly but no email arrived, check Lucy's spam/promotions folder first.

**Every company is "couldn't reach".**
Probably a transient network hiccup on the runner. Re-trigger the workflow manually. If it persists, one of the careers platforms may be down or rate-limiting GitHub's IP range; try again in a few hours.

**Same jobs keep appearing every day.**
The dedup is keyed on the job URL. If a company changes their URL scheme on every refresh (some do), edit `lib/state.py::job_id` to hash on `(company, title)` instead of URL.

**The AI is sending too many "worth a look" matches that aren't right.**
Add a few of the bad ones to `state/feedback.json` under `rejected_jobs` with the title + reason. The scorer feeds the last 8 rejections into the prompt as negative examples.

**The 7am-vs-8am thing.**
The cron is `0 6 * * *` UTC. In British Summer Time (late March to late October) that's 7am UK. In Greenwich Mean Time (winter) it lands at 6am UK. Most of the year is BST. If you want it nailed to 7am year-round, you'd need a self-hosted scheduler that knows about UK timezone rules.

## What's not in v1

- Automatic processing of "Not a match" / quote feedback replies (currently manual).
- A CV-fit signal (you asked to leave that out for now).
- SMS or WhatsApp delivery.

These are all reasonable v2 additions. The architecture supports them; nothing in v1 needs ripping up.
