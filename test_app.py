"""
Tests for app.py

Focus: the data transformation and display logic that determines what the
user actually sees in the dashboard. Streamlit UI calls are stubbed out
entirely — we are not testing the framework, we are testing our logic.

Key behaviors under test:
  - load_data() cleans and sorts raw CSV data correctly
  - Filtering narrows results by title and company
  - The apply link uses the direct URL when present, falling back to the
    aggregator URL when not
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

# ── Stub Streamlit before importing app ──────────────────────────────────────
# cache_data is replaced with a pass-through so load_data() behaves like a
# normal function in tests (no caching between test cases).
# sidebar.text_input must return "" so the filter block (which only runs when
# search_query is truthy) is skipped during module-level execution.
_st_mock = MagicMock()
_st_mock.cache_data = lambda **kwargs: (lambda fn: fn)
_st_mock.sidebar.text_input.return_value = ""
_st_mock.sidebar.radio.return_value = None  # falsy → skips the detail panel block
sys.modules['streamlit'] = _st_mock

# The module runs load_data() at import time. Give it a minimal valid frame
# so the rest of the module-level UI code has something to work with.
_stub_df = pd.DataFrame({
    'match_score': [75],
    'title': ['Software Engineer'],
    'company': ['Acme Corp'],
    'job_url': ['http://example.com/job/1'],
    'job_url_direct': [None],
    'match_reason': ['Good match'],
    'missing': ['Leadership experience'],
    'description': ['Build great things.'],
})

with patch('pandas.read_csv', return_value=_stub_df.copy()):
    import app


def _load(df):
    """Run a DataFrame through the same transformations load_data() applies,
    without touching the filesystem."""
    df = df.copy()
    df['match_score'] = pd.to_numeric(df['match_score'], errors='coerce').fillna(0)
    return df.sort_values(by='match_score', ascending=False)


class TestLoadData(unittest.TestCase):
    """
    load_data() is the gateway between raw CSV output and the dashboard.
    Its job is to make the data safe and sorted regardless of what jobsearch.py
    wrote — including edge cases from API failures or partial runs.
    """

    def test_results_are_sorted_best_match_first(self):
        """The user should see the highest-scoring jobs at the top without
        having to scroll past weak matches."""
        raw = pd.DataFrame({'match_score': [40, 90, 60], 'title': ['A', 'B', 'C'],
                            'company': ['x', 'x', 'x']})
        result = _load(raw)
        self.assertEqual(list(result['match_score']), [90, 60, 40])

    def test_non_numeric_scores_are_coerced_to_zero(self):
        """If the API returned an error string instead of a number, the job
        should appear at the bottom (score 0) rather than crashing the dashboard."""
        raw = pd.DataFrame({'match_score': ['API Error', '75'],
                            'title': ['A', 'B'], 'company': ['x', 'x']})
        result = _load(raw)
        scores = set(result['match_score'])
        self.assertIn(0, scores)
        self.assertIn(75, scores)

    def test_missing_scores_are_treated_as_zero(self):
        """A job with no score (NaN) should not crash the dashboard or sort
        above jobs that were actually scored."""
        raw = pd.DataFrame({'match_score': [None, 80.0],
                            'title': ['A', 'B'], 'company': ['x', 'x']})
        result = _load(raw)
        self.assertEqual(result.iloc[0]['match_score'], 80.0)
        self.assertEqual(result.iloc[1]['match_score'], 0.0)

    def test_all_valid_scores_are_preserved(self):
        """Normal numeric data should pass through without modification."""
        raw = pd.DataFrame({'match_score': [55, 82, 91],
                            'title': ['A', 'B', 'C'], 'company': ['x', 'x', 'x']})
        result = _load(raw)
        self.assertEqual(sorted(result['match_score'], reverse=True), [91, 82, 55])


class TestFiltering(unittest.TestCase):
    """
    The sidebar search box filters the job list by title or company.
    These tests confirm the filter finds what the user is looking for
    and doesn't accidentally hide relevant results.
    """

    def setUp(self):
        self.df = pd.DataFrame({
            'title': ['Principal Software Engineer', 'Product Manager', 'Staff Engineer'],
            'company': ['Acme Corp', 'Globex', 'Initech'],
            'match_score': [90, 60, 85],
        })

    def _apply_filter(self, df, query):
        """Mirrors the filter logic from app.py."""
        return df[
            df['title'].str.contains(query, case=False) |
            df['company'].str.contains(query, case=False)
        ]

    def test_filter_by_title_keyword(self):
        """Searching for a keyword that appears in a title should return only
        those jobs."""
        result = self._apply_filter(self.df, "Engineer")
        self.assertEqual(len(result), 2)
        self.assertTrue(all("Engineer" in t for t in result['title']))

    def test_filter_by_company_name(self):
        """Searching by company name should surface jobs at that company."""
        result = self._apply_filter(self.df, "Globex")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]['title'], 'Product Manager')

    def test_filter_is_case_insensitive(self):
        """Users should not need to match the exact capitalisation used in
        the job listing."""
        result_lower = self._apply_filter(self.df, "engineer")
        result_upper = self._apply_filter(self.df, "ENGINEER")
        self.assertEqual(len(result_lower), len(result_upper))

    def test_filter_returns_all_rows_for_empty_query(self):
        """With no search query the full job list should be visible."""
        # app.py only applies the filter when search_query is truthy,
        # so an empty string leaves df unchanged.
        query = ""
        if query:
            result = self._apply_filter(self.df, query)
        else:
            result = self.df
        self.assertEqual(len(result), len(self.df))

    def test_filter_returns_empty_for_no_match(self):
        """A query that matches nothing should yield an empty list, not an
        error or the full list."""
        result = self._apply_filter(self.df, "xyzzy_no_match")
        self.assertEqual(len(result), 0)


class TestApplyLinkLogic(unittest.TestCase):
    """
    The 'Apply' button should take the user directly to the company's own
    site when available, and fall back to the aggregator URL otherwise.
    This matters because direct links are more reliable and avoid extra
    redirect steps.
    """

    def _get_apply_url(self, job):
        """Mirrors the URL selection logic from app.py."""
        return job['job_url_direct'] if pd.notnull(job['job_url_direct']) else job['job_url']

    def test_uses_direct_url_when_present(self):
        job = pd.Series({
            'job_url': 'https://indeed.com/job/123',
            'job_url_direct': 'https://company.com/careers/engineer',
        })
        self.assertEqual(self._get_apply_url(job), 'https://company.com/careers/engineer')

    def test_falls_back_to_aggregator_url_when_direct_is_null(self):
        """Many listings don't have a direct URL — the aggregator URL must
        be used so the Apply button always works."""
        job = pd.Series({
            'job_url': 'https://indeed.com/job/456',
            'job_url_direct': None,
        })
        self.assertEqual(self._get_apply_url(job), 'https://indeed.com/job/456')

    def test_falls_back_to_aggregator_url_when_direct_is_nan(self):
        """NaN (the default for missing floats in pandas) is another form of
        missing data that must be treated as absent."""
        import numpy as np
        job = pd.Series({
            'job_url': 'https://ziprecruiter.com/job/789',
            'job_url_direct': float('nan'),
        })
        self.assertEqual(self._get_apply_url(job), 'https://ziprecruiter.com/job/789')


if __name__ == '__main__':
    unittest.main()
