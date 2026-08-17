# jbell ticket QA sampling → Google Sheet

Separate from the NXP_notebook (Slack+Zendesk → NotebookLM) project, but
built the same way and can reuse its Zendesk credentials and Google
service account.

Two recurring jobs plus a one-time historical pull, all writing to one
growing Google Sheet:

1. **Weekly ticket log** (`scripts/weekly_log.py`, every Friday 11pm ET) —
   appends one row per Zendesk ticket that got a new **public** comment
   from `jbell@nextpoint.com` since the last run, to the sheet's
   **"Ticket Log"** tab. Append-only, keeps growing forever.
2. **Biweekly QA sample** (`scripts/biweekly_sample.py`, every other Friday
   11pm ET) — randomly picks 6 tickets from the same population (tickets
   with a public jbell comment in the preceding 2-week window): one for
   evaluator John to score, one for evaluator Gabby to score, and 4 shared
   backups. Appends one row to the **"QA Sample"** tab, for manual scoring.
   "John" and "Gabby" are just the two evaluator slots (think eval1/eval2)
   — every ticket is drawn from the same jbell-commented population, and
   John/Gabby's own email addresses play no part in the selection.
3. **One-time historical backfill** (`scripts/backfill_sample.py`, run
   manually once) — does the same biweekly sample, but for every 2-week
   window from **2026-05-01 to 2026-08-14**, so the "QA Sample" tab starts
   with a full history instead of only rows going forward.

Like the NotebookLM project, this is 100% deterministic (no LLM calls) —
it only ever touches Zendesk's API and Google's Sheets API, using
credentials you control.

## What you need to set up (one-time)

### 1. Reuse (or create) Zendesk + Google credentials

If you already have the NXP_notebook project's `ZENDESK_SUBDOMAIN`,
`ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`, and `GOOGLE_SERVICE_ACCOUNT_JSON`
secrets, you can reuse the same values here — same Zendesk account, same
service account. Otherwise follow steps 3–4 in NXP_notebook's README to
create them.

### 2. Enable the Sheets API

The NotebookLM project's Google Cloud project has the Docs and Drive APIs
enabled, but **not** Sheets. In https://console.cloud.google.com, for that
same project: **APIs & Services → Library → Google Sheets API → Enable**.

### 3. Share the target Google Sheet with the service account

1. Create (or use an existing) Google Sheet — this becomes the one sheet
   both jobs write to, growing forever across its two tabs. Sheets don't
   have the tight per-file character limit Google Docs does (10 million
   cells vs. ~1,024,000 characters), so unlike the NotebookLM project's Doc
   pools, one Sheet is enough — no pool of multiple files to provision.
2. Share it with the service account's email address (from
   `GOOGLE_SERVICE_ACCOUNT_JSON`'s `client_email` field) with **Editor**
   access.
3. Copy the Sheet's ID from its URL:
   `https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit`

The "Ticket Log" and "QA Sample" tabs are created automatically (with
header rows) the first time each job runs — you don't need to create them
yourself.

### 4. Add secrets to this GitHub repo

**Settings → Secrets and variables → Actions → New repository secret:**

| Secret name | Value |
|---|---|
| `ZENDESK_SUBDOMAIN` | e.g. `nextpoint` |
| `ZENDESK_EMAIL` | the email tied to your Zendesk API token |
| `ZENDESK_API_TOKEN` | your Zendesk API token |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | paste the entire contents of the service account's JSON key file |
| `GOOGLE_SHEET_ID` | the Sheet ID from step 3 |

### 5. Run the one-time historical backfill

From the **Actions** tab → **"One-time historical QA sample backfill"** →
**Run workflow**. This populates the "QA Sample" tab with one row per
2-week window from 2026-05-01 through 2026-08-14 (see "How the windows are
computed" below), and records 2026-08-14 as the last-sampled window so the
regular biweekly job doesn't repeat it.

**Only run this once.** It always appends and isn't safe to re-run without
first clearing the "QA Sample" tab by hand — re-running it would duplicate
every historical row (unlike the NotebookLM project's own `backfill.py`,
which clears its target doc first specifically so it *can* be re-run
freely).

### 6. Turn on the weekly + biweekly workflows

Both are already scheduled — `weekly_log.yml` every Friday at 11pm ET
(fires as 03:00 UTC Saturday, so during EST it lands at 10pm ET instead;
see the comment in that file if you'd rather it stay pinned to 11pm ET
year-round), `biweekly_sample.yml` at the same time, 10 minutes later.
Both can also be triggered manually from the **Actions** tab to test them.

## How the windows are computed

**Biweekly job:** GitHub Actions cron has no native "every 2 weeks"
schedule, so `biweekly_sample.yml` actually fires every Friday, and
`scripts/biweekly_sample.py` decides for itself whether today is a sample
date — it only proceeds on Fridays that are an exact multiple of 14 days
after **2026-08-14** (the anchor: the most recent date the every-two-weeks
cadence should have landed on when this was set up). Every other Friday
it's a no-op. Next few sample dates from the anchor: 2026-08-28,
2026-09-11, 2026-09-25, ...

Each sample window covers the 14 days ending on that Friday, e.g. a run
landing on 2026-08-28 samples from tickets with a public jbell comment
between 2026-08-15 and 2026-08-28 inclusive. Dates are anchored to US
Eastern calendar days (matching "EOD" framing), not UTC — at 11pm ET the
UTC calendar date has already rolled to the next day.

**Historical backfill:** windows are counted backward from the anchor
(2026-08-01–08-14, 2026-07-18–07-31, ... down to 2026-05-09–05-22), the
same non-overlapping 14-day tiling the biweekly job uses going forward.
That leaves an 8-day leftover stretch, 2026-05-01–05-08, short of a full
2-week window — it's included as its own short row rather than dropped.

**If a window has fewer than 6 qualifying tickets** (should be rare):
slots are filled in order (John, then Gabby, then backup 1–4) and any
remaining slots are left blank, rather than reusing a ticket to force 6.
The job logs a warning when this happens.

## Who counts as "jbell"

Both jobs look for public comments authored by `jbell@nextpoint.com`,
hardcoded as `TARGET_AGENT_EMAIL` near the top of `scripts/weekly_log.py`
and `scripts/biweekly_sample.py`. Edit both if this ever needs to change
(**internal notes don't count** — only comments Zendesk itself marks
`public: true`, i.e. ones visible to the ticket requester).

## Sheet columns

**Ticket Log:** Week Ending, Ticket ID, Ticket Link, Subject, Requester,
Status, jbell Comment Date.

**QA Sample:** Pull Date, John's Ticket Link, Gabby's Ticket Link, Backup 1
Link, Backup 2 Link, Backup 3 Link, Backup 4 Link, John Score, John Notes,
Gabby Score, Gabby Notes. The four score/notes columns are left blank for
you to fill in by hand — nothing writes to them automatically.

## If a workflow fails on "Commit updated sync state" / "Commit seeded sample state"

Same self-healing behavior as the NotebookLM project: the push retries a
few times against the latest `main` on conflict (e.g. an overlapping
manual trigger), keeping this run's freshly-computed state. If it still
fails after retrying, nothing is corrupted — the next scheduled run just
starts from a slightly older cursor, which at worst means a ticket already
in the "Ticket Log" tab gets appended again (harmless duplication).
