# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A two-part job search automation tool:
1. **`jobsearch.py`** — Makes three Gemini API calls per run: (1) derives the best job title search term from the resume, (2) extracts the candidate's core tech stack, seniority, and secondary skills to build a personalized scoring rubric, then (3) scores each job 0–100 against the resume using that rubric. Scrapes Indeed, ZipRecruiter, Glassdoor, and Google Jobs. Outputs results to `my_job_report.csv` and tracks processed jobs in `processed_jobs.json` to avoid re-scoring.
2. **`app.py`** — Streamlit dashboard that reads `my_job_report.csv` and displays jobs sorted by AI match score, with filtering and full job description view.

## Running the Tools

**Scrape and score new jobs:**
```bash
python jobsearch.py path/to/resume.pdf
```
The resume path defaults to `RyanFisher-Resume.pdf` if no argument is provided.

**Launch the review dashboard:**
```bash
streamlit run app.py
```

## Dependencies

Install with pip:
```
streamlit
pandas
pymupdf        # imported as fitz
google-genai
jobspy
```

## Key Files at Runtime

- Resume PDF — passed as a command-line argument (`sys.argv[1]`); defaults to `RyanFisher-Resume.pdf`
- `my_job_report.csv` — Accumulated job results; appended to on each run
- `processed_jobs.json` — Set of already-scored job URLs to prevent duplicates

## Important Notes

- The Google Gemini API key is read from the `geminiapikey` environment variable (`os.environ.get("geminiapikey")`).
- The search term and scoring rubric are both generated dynamically from the resume via Gemini — there is no hardcoded candidate profile to update when changing resumes.
- The rubric extraction prompt is around `jobsearch.py:47` and the scoring prompt is around `jobsearch.py:110`. Edit the structure of those prompts (not the values) if you need to change how Gemini reasons about fit.
- `app.py` caches CSV data with a 60-second TTL (`@st.cache_data(ttl=60)`), so the dashboard auto-refreshes while `jobsearch.py` runs in parallel.
