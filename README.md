# JobSearcher

An AI-powered job search tool that scrapes listings from major job boards and scores each one against your resume using Google Gemini. It then presents results in an interactive dashboard so you can quickly focus on your best matches.

## How It Works

1. **`jobsearch.py`** — The scraper and scorer:
   - Reads your resume PDF and uses Gemini to determine the best job title to search for.
   - Uses Gemini to extract your core tech stack, seniority level, and secondary skills from the resume, and builds a personalized scoring rubric automatically.
   - Scrapes Indeed, ZipRecruiter, Glassdoor, and Google Jobs for recent remote postings.
   - Scores each job 0–100 against your resume using that personalized rubric.
   - Saves results to `my_job_report.csv` and remembers which jobs it has already scored so it never re-processes them.

2. **`app.py`** — The review dashboard:
   - Reads `my_job_report.csv` and displays jobs sorted by match score.
   - Lets you filter results and read full job descriptions in-browser.
   - Auto-refreshes every 60 seconds, so you can run the scraper and dashboard at the same time.

---

## Prerequisites

### 1. Install Python

If you don't have Python installed:

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download Python **3.10 or newer**.
2. Run the installer. **Important:** on Windows, check the box that says **"Add Python to PATH"** before clicking Install.
3. Verify it worked by opening a terminal and running:
   ```bash
   python --version
   ```
   You should see something like `Python 3.11.9`.

### 2. Get a Google Gemini API Key

1. Go to [aistudio.google.com](https://aistudio.google.com/) and sign in with a Google account.
2. Click **"Get API key"** → **"Create API key"**.
3. Copy the key — it looks like `AIzaSy...`.

### 3. Set the API Key as an Environment Variable

You need to make the key available to the tool without hardcoding it in the source code.

**Windows (Command Prompt):**
```cmd
setx geminiapikey "YOUR_API_KEY_HERE"
```
Close and reopen your terminal after running this — `setx` doesn't apply to the current session.

**Windows (PowerShell):**
```powershell
[System.Environment]::SetEnvironmentVariable("geminiapikey", "YOUR_API_KEY_HERE", "User")
```
Close and reopen PowerShell after running this.

**macOS / Linux:**

Add this line to your `~/.bashrc`, `~/.zshrc`, or equivalent shell config file:
```bash
export geminiapikey="YOUR_API_KEY_HERE"
```
Then reload it:
```bash
source ~/.bashrc
```

---

## Installation

1. **Clone or download this repository** to your machine.

2. **Open a terminal** and navigate to the project folder:
   ```bash
   cd path/to/JobSearcher
   ```

3. **Install the required Python packages:**
   ```bash
   pip install streamlit pandas pymupdf google-genai jobspy
   ```

---

## Usage

### Scrape and Score Jobs

Run the scraper with named arguments:

```bash
python jobsearch.py --resume path/to/your-resume.pdf [--hours 24] [--title "Staff Software Engineer"]
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--resume` | No | `RyanFisher-Resume.pdf` | Path to your resume PDF |
| `--hours` | No | `4` | How many hours back to search for new postings |
| `--title` | No | *(derived from resume)* | Job title to search for. Skips the Gemini search-term call when provided. |

Run `python jobsearch.py --help` to see this reference at any time.

Examples:
```bash
# Minimal — Gemini picks the search title from your resume
python jobsearch.py --resume resume.pdf

# Search the last 24 hours
python jobsearch.py --resume resume.pdf --hours 24

# Override the search title manually
python jobsearch.py --resume resume.pdf --hours 24 --title "Principal Software Engineer"
```

The tool will:
- Read your resume and pick a search term automatically (or use `--title` if provided).
- Extract your tech stack, seniority, and secondary skills to build a personalized scoring rubric — it will print what it derived so you can verify it looks right.
- Scrape the specified time window of remote job postings.
- Score each new job using your personalized rubric and append results to `my_job_report.csv`.

You can run this repeatedly — already-scored jobs are skipped automatically.

### View the Dashboard

In a separate terminal, launch the Streamlit dashboard:

```bash
streamlit run app.py
```

A browser window will open at `http://localhost:8501` showing your scored jobs sorted by match score.

### Running Both at Once

You can run the scraper and dashboard simultaneously. Open two terminal windows — start the dashboard in one, and run the scraper in the other. The dashboard will pick up new results within 60 seconds.

---

## Output Files

| File | Description |
|---|---|
| `my_job_report.csv` | All scored jobs. Safe to open in Excel. |
| `processed_jobs.json` | Tracks which job URLs have already been scored. Do not delete this unless you want to re-score everything. |

---

## Customizing for Your Resume

Everything — the search term, the rubric weights, and the scoring criteria — is derived automatically from your resume by Gemini. There is nothing to manually configure. Just pass in a different PDF and the tool adapts.

If the derived rubric doesn't look right when printed at startup, the most likely cause is a resume that is image-based (scanned) rather than text-based. Make sure your PDF has selectable text; if you can highlight words in a PDF viewer, it will work correctly.
