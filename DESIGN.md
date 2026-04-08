# System Design Document — JobSearcher

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [How AI Solves the Problem](#2-how-ai-solves-the-problem)
3. [Design Decisions](#3-design-decisions)
4. [Compliance and Safety](#4-compliance-and-safety)
5. [Rollout Plan](#5-rollout-plan)
6. [Monitoring and Impact Evaluation](#6-monitoring-and-impact-evaluation)

---

## 1. Problem Statement

Job searching on LinkedIn is largely a volume game with poor signal-to-noise. The platform surfaces many listings that look relevant on the surface but fail to match on seniority, technical stack, or role type. This forces candidates to manually read and evaluate dozens of job descriptions per session — a time-intensive, cognitively draining task with a low hit rate.

**Core pain points:**

- LinkedIn's own relevance ranking does not weight your specific technical skills or seniority — a Staff Engineer with a Python/Kubernetes background sees the same feed as a junior generalist.
- Job titles are inconsistently named across companies ("Staff Engineer," "Principal Engineer," "Senior Engineer II" may all describe the same level), making keyword searches brittle.
- Candidates waste significant time reading job descriptions that contain a disqualifying requirement buried in paragraph four.
- The apply-and-forget pattern makes it hard to track and compare options across multiple boards.

### The Sponsored Listing Problem

LinkedIn's job feed is ordered primarily by paid promotion, not relevance. Companies that pay more appear at the top — meaning every job seeker, regardless of background, sees largely the same set of sponsored listings first. This creates a structural crowding problem:

- The most visible jobs are the most applied-to jobs. A sponsored listing from a well-known company routinely accumulates **1,000–5,000+ applications within 24–48 hours** of posting.
- At that application volume, even a strong candidate has poor odds of a human ever reading their resume — most get filtered by ATS keyword matching or simply lost in the pile.
- Because the same listings dominate everyone's feed, job seekers are effectively competing in the same overcrowded pool rather than finding openings that genuinely match their profile.
- Smaller companies and newer postings that would be excellent fits are buried below the fold and never seen.

JobSearcher bypasses this dynamic by scraping job boards directly and sorting by AI-assessed fit — not by how much the employer paid to be seen. A four-hour-old listing from a less well-known company that is a 92/100 match will appear at the top of the dashboard, while a sponsored listing that is a poor fit will score low and be ignored.

**Goal:** Replace manual triage with an automated pipeline that scrapes multiple job boards, scores each listing against the candidate's actual resume, and surfaces only the strongest matches in a reviewable UI — so the candidate spends time applying, not searching.

---

## 2. How AI Solves the Problem

JobSearcher makes three targeted Gemini API calls per run, each replacing a distinct manual step:

### Call 1 — Search Term Generation (`jobsearch.py:95`)

**What it replaces:** The candidate manually brainstorming job title variants to search.

The resume text is sent to Gemini with a prompt that instructs it to produce 4–5 distinct, commonly-posted job titles that maximize coverage for the candidate's background. It is prompted to vary both seniority phrasing (e.g. "Staff Engineer" vs. "Principal Engineer") and domain focus (e.g. "Staff Software Engineer" vs. "Staff AI Engineer"). This replaces the manual, incomplete mental model most candidates have of their own searchable job titles.

If the user passes `--title` on the command line, this call is skipped entirely and the provided title is used as a single search term.

### Call 2 — Rubric Extraction (`jobsearch.py:112`)

**What it replaces:** A recruiter's manual read of the resume to identify the candidate's core stack, seniority, and secondary skills.

The resume text is sent to Gemini and it returns a structured JSON rubric:

```json
{
  "role_type": "software engineering",
  "core_stack": "C#, .NET, Python, Kubernetes, Azure",
  "seniority": "Staff Engineer",
  "secondary_skills": "DevOps, distributed systems, technical leadership"
}
```

This rubric is what drives scoring in Call 3. Generating it from the resume (rather than hardcoding it) means the tool works for any candidate without configuration.

### Call 3 — Per-Job Scoring (`jobsearch.py:178`)

**What it replaces:** The candidate reading every job description and mentally assessing fit.

For each new listing, the job description (truncated to 8,000 characters) and the rubric from Call 2 are sent to Gemini with a two-step prompt:

1. **Knockout checks** — applied first. If the role is outside the candidate's function (e.g. a recruiter role surfaced because it mentions "engineers"), the score is capped at 15. If there is a hard unmet requirement (PhD, active security clearance, 10+ years in a specific stack the resume doesn't show), the score is capped at 30.
2. **Rubric scoring** — if no knockout applies, a weighted score is computed: 50% core stack alignment, 30% seniority match, 20% secondary skills.

The response is a JSON object with a numeric score, a plain-English reason, and a short list of the most critical gaps. These three fields are stored alongside the job listing in `my_job_report.csv`.

### Architecture Diagram

```
Resume PDF
    │
    ▼
[jobsearch.py]
    │
    ├─► Gemini Call 1: Search Term Generation
    │       └─► ["Staff Software Engineer", "Principal Engineer", ...]
    │
    ├─► Gemini Call 2: Rubric Extraction
    │       └─► {role_type, core_stack, seniority, secondary_skills}
    │
    ├─► JobSpy: Scrape Indeed / ZipRecruiter / Glassdoor / Google Jobs
    │       └─► Raw job listings (deduplicated)
    │
    └─► Gemini Call 3 (per job): Score + Reason + Gaps
            └─► my_job_report.csv
                    │
                    ▼
              [app.py / Streamlit]
                    │
                    └─► Sidebar: ranked job list + filter
                    └─► Main panel: score, reasoning, gaps, full JD, apply link
```

---

## 3. Design Decisions

### 3.1 Resume-Driven, No Hardcoded Profile

The scoring rubric and search terms are generated from the resume on every run. There is no YAML config or database of candidate preferences to maintain. This means the tool works immediately for any candidate who has a text-based PDF resume, and it adapts automatically when the resume is updated.

**Trade-off:** The quality of results depends on the quality of the resume PDF. Image-based (scanned) resumes will produce no text and therefore no meaningful rubric.

### 3.2 Append-Only CSV + URL History File

Results are appended to `my_job_report.csv` rather than overwritten. Processed job URLs are tracked in `processed_jobs.json`. This means:

- The dashboard always shows the full accumulated history.
- The scraper never wastes API quota re-scoring a job it has already seen.
- The user can run the tool repeatedly (e.g. on a cron every few hours) without managing state manually.

**Trade-off:** The CSV grows over time. For very long job searches it may need periodic archiving.

### 3.3 Multi-Board Scraping with Cross-Term Deduplication

JobSpy is used to scrape four boards simultaneously (Indeed, ZipRecruiter, Glassdoor, Google Jobs). Multiple search terms are run independently and the results are combined. Deduplication is performed on `(company, title)` after all scrapes complete, preferring the copy that has a direct application URL.

**Trade-off:** Some legitimate listings with slight title variations at the same company may be collapsed. This is an acceptable false-positive on deduplication given the benefit of reducing redundant scoring API calls.

### 3.4 Knockout-Then-Rubric Scoring Prompt Structure

The scoring prompt is structured as a two-step chain-of-thought: knockout checks first, then rubric scoring only if no knockout fires. This prevents Gemini from "averaging" a disqualifying issue into a middling score (e.g. a PhD requirement producing a 45 instead of a cap at 30). Knockouts are explicit and reason-documented so the candidate sees why a score was capped.

### 3.5 Gemini 2.5 Flash Lite

`gemini-2.5-flash-lite` is used for all three calls. It is fast and cheap enough to score 50–100 listings per run within a reasonable time budget, while still producing structured JSON reliably when `response_mime_type='application/json'` is set.

### 3.6 Streamlit for the Dashboard

Streamlit was chosen for the review UI because it requires no frontend knowledge to operate or extend, runs locally with a single command, and can be served as a browser app without a separate web server. The 60-second cache TTL means the dashboard auto-refreshes while the scraper runs in parallel in another terminal — no manual reload required.

### 3.7 Separation of Concerns: Scraper vs. Dashboard

The scraper (`jobsearch.py`) and dashboard (`app.py`) are fully decoupled — they communicate only through `my_job_report.csv`. This means:

- Either component can be run independently.
- The scraper can be scheduled (e.g. via Windows Task Scheduler or cron) without the dashboard being open.
- The dashboard can be left open permanently and will always reflect the latest data.

---

## 4. Compliance and Safety

### 4.1 Web Scraping

JobSearcher uses [JobSpy](https://github.com/Bunsly/JobSpy) to scrape Indeed, ZipRecruiter, Glassdoor, and Google Jobs. Scraping public job boards sits in a legal gray area. Key mitigations:

- The tool scrapes at a low rate (one request cycle per run, not continuous polling).
- It is intended for personal use by the job seeker, not bulk data resale.
- Users should review the Terms of Service of each platform before use.
- The `--hours` parameter defaults to 4 hours, limiting the volume of data fetched per run.

### 4.2 Resume Privacy

The full resume text is sent to the Google Gemini API as part of each prompt. This means resume content (employment history, contact information, skills) is transmitted to and processed by Google's infrastructure under Google's data processing terms.

- Users should review [Google's Gemini API Terms of Service](https://ai.google.dev/gemini-api/terms) and [Privacy Policy](https://policies.google.com/privacy) before use.
- The resume is never stored in the tool itself beyond the in-memory string used during a single run.
- No resume data is written to `my_job_report.csv` or `processed_jobs.json`.

### 4.3 API Key Security

The Gemini API key is read from an environment variable (`GeminiApiKey`) and is never written to any file, logged to the console, or committed to source control. Users should ensure their API key is not exposed in shell history or shared terminals.

### 4.4 No Automated Application Submission

The tool surfaces job listings and direct apply links but does not submit applications on the user's behalf. All application actions are manual. This is intentional — automated mass-application would violate job board terms and reduce application quality.

### 4.5 Score Transparency

Every score comes with a plain-English reason and a list of gaps. The tool is designed to augment the candidate's judgment, not replace it. A score of 85 is a recommendation to look closer — not a guarantee of fit. Candidates should read the full JD before applying.

---

## 5. Rollout Plan

JobSearcher is a personal productivity tool intended for individual use, not a multi-tenant SaaS product. The rollout stages below reflect progressive personal adoption rather than a staged enterprise deployment.

### Stage 1 — Single User, Manual Runs (Current)

- Run `jobsearch.py` manually when beginning a job search session.
- Review results in `app.py` dashboard.
- Validate that the rubric Gemini derives from the resume is accurate (printed at startup).
- Tune `--hours` and `--results` parameters based on typical result volume.

### Stage 2 — Scheduled Runs

- Set up a scheduled task (Windows Task Scheduler or cron on macOS/Linux) to run `jobsearch.py` automatically every 4–6 hours.
- Leave the Streamlit dashboard open; it will pick up new results within 60 seconds.
- Example Windows Task Scheduler command:
  ```
  python C:\path\to\JobSearcher\jobsearch.py --resume C:\path\to\resume.pdf --hours 6
  ```

### Stage 3 — Multi-Resume / Multi-Role Support

- Pass different resumes via `--resume` to support targeted searches for different role types (e.g. individual contributor vs. engineering manager).
- Use `--title` to pin searches for specific roles when a target company or title is already known.

### Stage 4 — Shared Use (Optional)

- If sharing with a small group (e.g. a job search cohort), each user runs the tool locally with their own resume and API key.
- No shared infrastructure is required — the CSV and JSON files are local per user.
- Users can optionally share their `my_job_report.csv` exports for discussion without sharing resume content.

---

## 6. Monitoring and Impact Evaluation

Because this is a personal tool, "monitoring" means tracking whether the tool is actually improving the job search experience over time.

### 6.1 Quality of Derived Rubric

At each run, the tool prints the derived rubric:

```
Rubric — Role: software engineering | Core: C#, .NET, Python, Kubernetes | Seniority: Staff Engineer | Secondary: DevOps, Azure
```

**Check:** Does the printed rubric accurately reflect your background? If the core stack is wrong or the seniority is off, the scoring will be systematically biased. This is the most important quality gate — verify it on each new resume version.

### 6.2 Score Distribution

After accumulating a few hundred scored jobs, review the distribution of `match_score` values in `my_job_report.csv`:

- A healthy distribution should have most jobs below 50 (correctly filtered out), a moderate band in 50–75, and a small number of genuine 80+ matches.
- If nearly all jobs score 70+, the knockout checks may not be firing — check that `role_type` in the rubric is specific enough.
- If nearly all jobs score below 40, the core stack aliases may be mismatching — check the rubric and verify technology normalization.

### 6.3 Application Outcome Tracking

The tool does not currently track outcomes (applied, phone screen, offer). To evaluate impact, maintain a simple log alongside the tool:

| Date | Company | Title | Match Score | Applied? | Outcome |
|------|---------|-------|-------------|----------|---------|
| 2026-04-01 | Acme Corp | Staff Engineer | 87 | Yes | Phone screen |
| 2026-04-01 | Foo Inc | Principal SWE | 92 | Yes | Rejected |

Over time, compare outcomes for jobs above vs. below a score threshold (e.g. 75). If high-scoring jobs are not converting, examine whether the missing/gaps fields correlate with rejection reasons.

### 6.4 Time Saved

Track the approximate time spent on job search per week before and after adopting the tool. The target is to eliminate manual scanning of job board feeds almost entirely, spending time only on reviewing the top 10–15 matches the tool surfaces each day.

### 6.5 API Cost

The Gemini API has a free tier (rate-limited) and a paid tier. Monitor usage in [Google AI Studio](https://aistudio.google.com/) if running the tool frequently. With default settings (50 results, 4-5 search terms), a typical run scores ~50–100 new jobs, which is well within the free tier limits for personal use.
