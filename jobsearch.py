import os
import sys
import json
import time
import argparse
import fitz  # PyMuPDF
import pandas as pd
from google import genai
from jobspy import scrape_jobs

# --- CONFIGURATION ---
API_KEY = os.environ.get("GeminiApiKey")

parser = argparse.ArgumentParser(
    description="Scrape job boards and score listings against your resume using Gemini AI.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
examples:
  python jobsearch.py --resume resume.pdf
  python jobsearch.py --resume resume.pdf --hours 24
  python jobsearch.py --resume resume.pdf --hours 24 --results 50
  python jobsearch.py --resume resume.pdf --hours 24 --title "Staff Software Engineer"
    """
)
parser.add_argument("--resume", default="RyanFisher-Resume.pdf",
                    help="Path to your resume PDF (default: RyanFisher-Resume.pdf)")
parser.add_argument("--hours", type=int, default=4,
                    help="How many hours back to search for postings (default: 4)")
parser.add_argument("--results", type=int, default=50,
                    help="Number of results to fetch per search term (default: 50)")
parser.add_argument("--title", default=None,
                    help="Job title to search for. If omitted, Gemini generates multiple variations from your resume.")
args = parser.parse_args()

RESUME_PATH = args.resume
HOURS_OLD = args.hours
RESULTS_WANTED = args.results
HISTORY_FILE = "processed_jobs.json"
REPORT_FILE = "my_job_report.csv"

# --- HELPER FUNCTIONS ---
def extract_resume_text(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        text = "".join([page.get_text() for page in doc])
        doc.close()
        return " ".join(text.split())
    except Exception as e:
        print(f"PDF Error: {e}")
        return None

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_history(history_set):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(list(history_set), f)

def gemini_generate(client, prompt, response_mime_type=None, max_retries=3, retry_delay=5):
    config = {'response_mime_type': response_mime_type} if response_mime_type else {}
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=config
            )
            return response
        except Exception as e:
            if "503" in str(e) or "overloaded" in str(e).lower():
                wait_time = retry_delay * (2 ** attempt)
                print(f"Server overloaded. Retrying in {wait_time}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                raise
    raise RuntimeError(f"Gemini API unavailable after {max_retries} retries.")

# --- MAIN EXECUTION ---
client = genai.Client(api_key=API_KEY)
resume_text = extract_resume_text(RESUME_PATH)
history = load_history()

if not resume_text:
    print("Could not load resume. Exiting.")
    exit()

if args.title:
    search_terms = [args.title]
    print(f"Using search term from argument: '{args.title}'")
else:
    print("Generating search terms from resume...")
    search_prompt = f"""
You are a job search expert. Based on the resume below, produce 4-5 distinct job title search terms
that together cast the widest net for roles this candidate is genuinely qualified for.

Rules:
- Return a JSON array of strings — titles only, no explanation.
- Vary seniority phrasing (e.g. "Staff Engineer" AND "Principal Engineer") and domain focus
  (e.g. "Staff Software Engineer" AND "Staff AI Engineer") to maximize coverage.
- Each title should be a real, commonly-posted job title that will surface relevant results.

Resume: {resume_text[:3000]}
"""
    search_terms_response = gemini_generate(client, search_prompt, response_mime_type='application/json')
    search_terms = json.loads(search_terms_response.text)
    print(f"Using search terms: {search_terms}")

print("Generating scoring rubric from resume...")
rubric_prompt = f"""
You are a career expert. Based on the resume below, extract the candidate's profile for job matching.

Return JSON with exactly these fields:
{{
  "core_stack": "comma-separated list of their primary technical skills/languages/frameworks",
  "seniority": "their level, e.g. Principal Engineer, Staff Engineer, Senior Engineer",
  "secondary_skills": "comma-separated list of secondary skills like DevOps, management, cloud, etc."
}}

Resume: {resume_text[:3000]}
"""
rubric_response = gemini_generate(client, rubric_prompt, response_mime_type='application/json')
rubric = json.loads(rubric_response.text)
core_stack = rubric.get('core_stack', 'relevant technical skills')
seniority = rubric.get('seniority', 'Senior Engineer')
secondary_skills = rubric.get('secondary_skills', 'DevOps, Management')
print(f"Rubric — Core: {core_stack} | Seniority: {seniority} | Secondary: {secondary_skills}")

print("Searching Indeed, ZipRecruiter, Glassdoor & Google Jobs...")
all_frames = []
for term in search_terms:
    print(f"  Searching '{term}'...")
    results = scrape_jobs(
        site_name=["indeed", "zip_recruiter", "glassdoor", "google"],
        search_term=term,
        location="Remote",
        hours_old=HOURS_OLD,
        results_wanted=RESULTS_WANTED
    )
    all_frames.append(results)

jobs = pd.concat(all_frames, ignore_index=True)

# Deduplicate cross-site and cross-term listings by company + title, keeping
# the copy with a direct URL when available (better apply link).
jobs['_has_direct'] = jobs['job_url_direct'].notna()
jobs = (jobs
        .sort_values('_has_direct', ascending=False)
        .drop_duplicates(subset=['company', 'title'], keep='first')
        .drop(columns='_has_direct'))
print(f"Scraped {len(jobs)} unique listings after removing duplicates.")

# Filter out jobs we've already scored
new_jobs = jobs[~jobs['job_url'].isin(history)].copy()
already_seen = len(jobs) - len(new_jobs)
print(f"{already_seen} already scored, {len(new_jobs)} new to score.")

if not new_jobs.empty:
    results_data = []
    for index, row in new_jobs.iterrows():
        raw_description = str(row.get('description', ""))

        # If the description is too short to be a real JD, skip it
        if len(raw_description) < 10:
            print(f"Skipping {row['title']} - No description found.")
            results_data.append({'match_score': 0, 'match_reason': 'Missing description'})
            continue

        print(f"Scoring: {row['title']}...")

        prompt = f"""
        You are a strict career matching expert. Your job is to protect the candidate's
        time by filtering out roles that are not a genuine fit.

        Candidate profile:
        - Core Technical Stack: {core_stack}
        - Seniority: {seniority}
        - Secondary Skills: {secondary_skills}

        STEP 1 — KNOCKOUT CHECKS (apply these first; if any trigger, use the capped score):
        - If the JD is not an individual-contributor software engineering role
          (e.g. it is a recruiter, sales, program manager, data analyst, or purely
          management role), cap the score at 15. Sharing technical vocabulary does
          not make a non-engineering role a match.
        - If the JD lists a hard requirement the candidate clearly cannot meet
          (e.g. requires a PhD and the resume shows no PhD, requires an active
          security clearance, requires 10+ years in a specific stack the resume
          does not show), cap the score at 30.

        STEP 2 — RUBRIC SCORING (only if no knockout applies):
        Score 0-100 using these weights:
        - 50%: Core technical stack alignment ({core_stack})
        - 30%: Seniority match ({seniority})
        - 20%: Secondary skills ({secondary_skills})

        SCORING RULES:
        - Missing secondary skills alone should not drop a score below 70 if
          the core stack and seniority are strong matches.
        - A skills overlap due to shared industry vocabulary (e.g. a recruiter
          role mentioning "engineers" or "sprints") is NOT a technical stack match.
        - Score 90+ only if this is a role the candidate would very likely get
          through screening with no red flags.
        - Score 70-89 for strong contenders with minor gaps.
        - Score 50-69 for possible fits with notable gaps.
        - Score below 50 for poor fits (but only after knockout checks pass).

        Resume: {resume_text[:2000]}
        JD: {raw_description[:3000]}

        Return JSON:
        {{
          "score": integer,
          "reason": "Short summary of why this score was given, naming any knockout that was applied",
          "missing": ["List 2-3 most critical missing items or the knockout reason"]
        }}
        """

        try:
            response = gemini_generate(client, prompt, response_mime_type='application/json')
            res_json = json.loads(response.text)
            results_data.append({
                'match_score': res_json.get('score', 0),
                'match_reason': res_json.get('reason', 'N/A'),
                'missing': ", ".join(res_json.get('missing', []))
            })
        except RuntimeError:
            print(f"Skipping {row['title']} after retries exhausted.")
            results_data.append({'match_score': 0, 'match_reason': 'Server Timeout', 'missing': ''})
        except Exception as e:
            print(f"Permanent Error: {e}")
            results_data.append({'match_score': 0, 'match_reason': 'API Error', 'missing': ''})

        time.sleep(1)

    # Add the new columns to the dataframe
    results_df = pd.DataFrame(results_data)
    new_jobs['match_score'] = results_df['match_score'].values
    new_jobs['match_reason'] = results_df['match_reason'].values
    new_jobs['missing'] = results_df['missing'].values

    # Sort by score and save as usual
    new_jobs = new_jobs.sort_values(by='match_score', ascending=False)

    # Save results
    history.update(new_jobs['job_url'].tolist())
    save_history(history)

    final_columns = [
        'job_url',
        'job_url_direct',
        'title',
        'company',
        'match_score',
        'match_reason',
        'missing',
        'description'
    ]

    # Check if columns exist (JobSpy sometimes varies output)
    available_cols = [c for c in final_columns if c in new_jobs.columns]
    output_df = new_jobs[available_cols].sort_values(by='match_score', ascending=False)

    # Append to CSV
    mode = 'a' if os.path.exists(REPORT_FILE) else 'w'
    header = not os.path.exists(REPORT_FILE)
    output_df.to_csv(REPORT_FILE, mode=mode, header=header, index=False)

    print(f"Done! {len(new_jobs)} jobs added to {REPORT_FILE}")
else:
    print("No new jobs found.")
