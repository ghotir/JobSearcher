import os
import sys
import json
import time
import fitz  # PyMuPDF
import pandas as pd
from google import genai
from jobspy import scrape_jobs

# --- CONFIGURATION ---
API_KEY = os.environ.get("geminiapikey")
RESUME_PATH = sys.argv[1] if len(sys.argv) > 1 else "RyanFisher-Resume.pdf"
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

# --- MAIN EXECUTION ---
client = genai.Client(api_key=API_KEY)
resume_text = extract_resume_text(RESUME_PATH)
history = load_history()

if not resume_text:
    print("Could not load resume. Exiting.")
    exit()

print("Generating search term from resume...")
search_prompt = f"""
You are a job search expert. Based on the resume below, produce the single best job title search term
to find roles this candidate is qualified for and would be a strong match for.

Rules:
- Return ONLY the job title string, nothing else — no punctuation, no explanation.
- Choose a broadly-used title (e.g. "Principal Software Engineer") that will surface the most relevant postings.
- Reflect the candidate's actual seniority and primary technical domain.

Resume: {resume_text[:3000]}
"""
search_term_response = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents=search_prompt
)
search_term = search_term_response.text.strip()
print(f"Using search term: '{search_term}'")

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
rubric_response = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents=rubric_prompt,
    config={'response_mime_type': 'application/json'}
)
rubric = json.loads(rubric_response.text)
core_stack = rubric.get('core_stack', 'relevant technical skills')
seniority = rubric.get('seniority', 'Senior Engineer')
secondary_skills = rubric.get('secondary_skills', 'DevOps, Management')
print(f"Rubric — Core: {core_stack} | Seniority: {seniority} | Secondary: {secondary_skills}")

print("Searching Indeed, ZipRecruiter, Glassdoor & Google Jobs...")
jobs = scrape_jobs(
    site_name=["indeed", "zip_recruiter", "glassdoor", "google"],
    search_term=search_term,
    location="Remote",
    hours_old=4,
    results_wanted=50
)

# Filter out jobs we've already scored
new_jobs = jobs[~jobs['job_url'].isin(history)].copy()

if not new_jobs.empty:
    results_data = [] # To store our structured scores
    max_retries = 3
    retry_delay = 5  # Start with 5 seconds
    for index, row in new_jobs.iterrows():
        raw_description = str(row.get('description', ""))
    
        # If the description is too short to be a real JD, skip it
        if len(raw_description) < 10:
            print(f"Skipping {row['title']} - No description found.")
            results_data.append({'match_score': 0, 'match_reason': 'Missing description'})
            continue  
        
        print(f"Scoring: {row['title']}...")
        
        prompt = f"""
        You are a career matching expert. Compare the Resume to the Job Description (JD).
        Assign a score from 0-100 based on this weighted rubric:
        - 50%: Core Technical Stack ({core_stack}).
        - 30%: Seniority/Experience Level ({seniority}).
        - 20%: Secondary Skills ({secondary_skills}).

        SCORING RULES:
        - If they have the Core Tech but lack the Secondary Skills, they can still score up to an 80.
        - Only score below 50 if the Core Technical Stack is a complete mismatch.
        - Be realistic: A 90+ is a "Must Interview," 70-80 is a "Strong Contender."

        Resume: {resume_text[:2000]}
        JD: {raw_description[:3000]}

        Return JSON:
        {{
          "score": integer,
          "reason": "Short summary of why this score was given",
          "missing": ["List 2-3 most critical missing items"]
        }}
        """
        
        success = False
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite", 
                    contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
                # If successful, parse and break retry loop
                res_json = json.loads(response.text)
                results_data.append({
                    'match_score': res_json.get('score', 0),
                    'match_reason': res_json.get('reason', 'N/A'),
                    'missing': ", ".join(res_json.get('missing', []))
                })
                success = True
                break 
                
            except Exception as e:
                if "503" in str(e) or "overloaded" in str(e).lower():
                    wait_time = retry_delay * (2 ** attempt) # 5s, 10s, 20s...
                    print(f"Server overloaded. Retrying in {wait_time}s (Attempt {attempt+1}/{max_retries})...")
                    time.sleep(wait_time)
                else:
                    print(f"Permanent Error: {e}")
                    results_data.append({'match_score': 0, 'match_reason': 'API Error', 'missing': ''})
                    success = True # Move on to next job
                    break

        if not success:
            print(f"Skipping {row['title']} after {max_retries} failed retries.")
            results_data.append({'match_score': 0, 'match_reason': 'Server Timeout', 'missing': ''})

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

    # 3. Filter and Save
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