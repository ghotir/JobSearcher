import streamlit as st
import pandas as pd

st.set_page_config(layout="wide", page_title="Job Hunter AI")

# --- Load Data ---
@st.cache_data(ttl=60) # Refreshes every minute
def load_data():
    df = pd.read_csv("my_job_report.csv")
    # Clean up and ensure scores are numeric
    df['match_score'] = pd.to_numeric(df['match_score'], errors='coerce').fillna(0)
    return df.sort_values(by='match_score', ascending=False)

df = load_data()

# --- Sidebar: Job List ---
st.sidebar.title(f"🎯 Top Matches ({len(df)})")

# Search/Filter in Sidebar
search_query = st.sidebar.text_input("Filter by title or company", "")
if search_query:
    df = df[df['title'].str.contains(search_query, case=False) | 
            df['company'].str.contains(search_query, case=False)]

# Create the selection list
# We combine score and title for the label
job_options = [f"[{int(row.match_score)}] {row.title} @ {row.company}" for _, row in df.iterrows()]
selected_option = st.sidebar.radio("Select a job to review:", job_options)

# --- Main Panel: Job Details ---
if selected_option:
    # Get the index of the selected job
    idx = job_options.index(selected_option)
    job = df.iloc[idx]

    # Header section
    st.title(job['title'])
    st.subheader(f"{job['company']}")
    
    col1, col2 = st.columns(2)
    with col1:
        st.link_button("🚀 Apply on Direct Site", job['job_url_direct'] if pd.notnull(job['job_url_direct']) else job['job_url'])
    with col2:
        st.metric(label="Match Score", value=f"{int(job.match_score)}/100")

    st.divider()

    # AI Analysis Section
    st.markdown("### 🤖 AI Analysis")
    st.info(f"**Reasoning:** {job['match_reason']}")
    if 'missing' in job and pd.notnull(job['missing']):
        st.warning(f"**Missing/Gaps:** {job['missing']}")

    st.divider()

    # Full Description Section
    st.markdown("### 📄 Full Job Description")
    st.text_area(label="", value=job['description'], height=500)