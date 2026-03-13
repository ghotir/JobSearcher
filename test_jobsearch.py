"""
Tests for jobsearch.py

Focus: intent and correctness of the helper functions.
The module-level scraping/scoring pipeline is not tested here because
it depends entirely on live external services (Gemini, job boards).

To import the module cleanly, external dependencies are stubbed in
sys.modules before the first import so the module-level execution code
runs without hitting real APIs or the filesystem.
"""
import sys
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

import pandas as pd

# ── Stub heavy external dependencies before importing jobsearch ──────────────
_fitz_mock = MagicMock()
_genai_mock = MagicMock()
_jobspy_mock = MagicMock()

# 'from google import genai' resolves via sys.modules['google'].genai, so we
# need both the package and submodule entries to point at the same mock.
_google_mock = MagicMock()
_google_mock.genai = _genai_mock
sys.modules.setdefault('fitz', _fitz_mock)
sys.modules.setdefault('google', _google_mock)
sys.modules.setdefault('google.genai', _genai_mock)
sys.modules.setdefault('jobspy', _jobspy_mock)

# Make fitz.open return a single-page doc with dummy text so that
# extract_resume_text succeeds and the module doesn't call exit().
_mock_page = MagicMock()
_mock_page.get_text.return_value = "dummy resume text"
_mock_doc = MagicMock()
_mock_doc.__iter__ = lambda self: iter([_mock_page])
_fitz_mock.open.return_value = _mock_doc

# scrape_jobs returns an empty frame so the scoring loop is skipped.
_jobspy_mock.scrape_jobs.return_value = pd.DataFrame({'job_url': pd.Series([], dtype=str)})

# Gemini client returns valid responses for the two module-level calls
# (search term generation and rubric extraction).
_mock_client = MagicMock()
_genai_mock.Client.return_value = _mock_client

_search_resp = MagicMock()
_search_resp.text = "Principal Software Engineer"

_rubric_resp = MagicMock()
_rubric_resp.text = json.dumps({
    "core_stack": "Python, Django",
    "seniority": "Principal Engineer",
    "secondary_skills": "DevOps, AWS",
})

_mock_client.models.generate_content.side_effect = [_search_resp, _rubric_resp]

with patch.dict('os.environ', {'GeminiApiKey': 'fake-key'}), \
     patch('sys.argv', ['jobsearch.py', 'dummy.pdf']):
    import jobsearch


class TestGeminiGenerate(unittest.TestCase):
    """
    gemini_generate wraps every Gemini call with exponential-backoff retry
    logic. These tests verify that the retry policy behaves correctly under
    different failure modes.
    """

    def _make_client(self, side_effects):
        """Return a fresh mock client whose generate_content raises or returns
        the given side_effects in order."""
        client = MagicMock()
        client.models.generate_content.side_effect = side_effects
        return client

    def test_returns_response_immediately_on_success(self):
        """A healthy API call should return on the first attempt with no delay."""
        expected = MagicMock()
        client = self._make_client([expected])

        with patch('time.sleep') as mock_sleep:
            result = jobsearch.gemini_generate(client, "prompt")

        self.assertIs(result, expected)
        mock_sleep.assert_not_called()

    def test_retries_on_503_and_eventually_succeeds(self):
        """A transient 503 should trigger a retry and return the next response."""
        expected = MagicMock()
        client = self._make_client([
            Exception("503 Service Unavailable"),
            expected,
        ])

        with patch('time.sleep'):
            result = jobsearch.gemini_generate(client, "prompt", retry_delay=1)

        self.assertIs(result, expected)
        self.assertEqual(client.models.generate_content.call_count, 2)

    def test_retries_on_overloaded_error_and_eventually_succeeds(self):
        """'overloaded' in the error message should also trigger a retry."""
        expected = MagicMock()
        client = self._make_client([
            Exception("model is overloaded, please try again"),
            expected,
        ])

        with patch('time.sleep'):
            result = jobsearch.gemini_generate(client, "prompt", retry_delay=1)

        self.assertIs(result, expected)

    def test_raises_runtime_error_after_all_retries_exhausted(self):
        """When every attempt returns a 503, a RuntimeError should be raised
        so the caller knows the API is unavailable — not silently ignored."""
        client = self._make_client([
            Exception("503 overloaded"),
            Exception("503 overloaded"),
            Exception("503 overloaded"),
        ])

        with patch('time.sleep'), self.assertRaises(RuntimeError):
            jobsearch.gemini_generate(client, "prompt", max_retries=3, retry_delay=1)

        # All three attempts were made.
        self.assertEqual(client.models.generate_content.call_count, 3)

    def test_does_not_retry_permanent_errors(self):
        """A non-503 error (e.g. auth failure) should propagate immediately
        without wasting time on retries."""
        auth_error = PermissionError("invalid API key")
        client = self._make_client([auth_error])

        with patch('time.sleep') as mock_sleep, self.assertRaises(PermissionError):
            jobsearch.gemini_generate(client, "prompt", retry_delay=1)

        mock_sleep.assert_not_called()
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_exponential_backoff_wait_times(self):
        """Each retry should wait longer than the last (5s → 10s → 20s)."""
        client = self._make_client([
            Exception("503"),
            Exception("503"),
            Exception("503"),
        ])

        with patch('time.sleep') as mock_sleep, self.assertRaises(RuntimeError):
            jobsearch.gemini_generate(client, "prompt", max_retries=3, retry_delay=5)

        wait_times = [c.args[0] for c in mock_sleep.call_args_list]
        self.assertEqual(wait_times, [5, 10, 20])

    def test_passes_json_mime_type_in_config_when_requested(self):
        """When response_mime_type is set, it must reach the API call so that
        Gemini returns structured JSON instead of markdown."""
        expected = MagicMock()
        client = self._make_client([expected])

        jobsearch.gemini_generate(client, "prompt", response_mime_type='application/json')

        _, kwargs = client.models.generate_content.call_args
        self.assertEqual(kwargs['config'], {'response_mime_type': 'application/json'})

    def test_omits_config_when_no_mime_type(self):
        """Without a mime type, config should be empty so we don't force a
        response format on open-ended prompts."""
        expected = MagicMock()
        client = self._make_client([expected])

        jobsearch.gemini_generate(client, "prompt")

        _, kwargs = client.models.generate_content.call_args
        self.assertEqual(kwargs['config'], {})


class TestExtractResumeText(unittest.TestCase):
    """
    extract_resume_text is the entry point for all resume data. These tests
    confirm it handles both the happy path and failure modes correctly.
    """

    def test_extracts_text_from_pdf(self):
        """Text from every page should be returned as a single string."""
        mock_page1 = MagicMock()
        mock_page1.get_text.return_value = "Hello world"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "Foo bar"
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page1, mock_page2])

        with patch('fitz.open', return_value=mock_doc):
            result = jobsearch.extract_resume_text("resume.pdf")

        self.assertIn("Hello world", result)
        self.assertIn("Foo bar", result)

    def test_normalizes_whitespace(self):
        """Extra whitespace and newlines in the PDF should be collapsed so the
        Gemini prompt doesn't waste tokens on blank space."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = "  Senior   Engineer\n\nPython  "
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])

        with patch('fitz.open', return_value=mock_doc):
            result = jobsearch.extract_resume_text("resume.pdf")

        self.assertEqual(result, "Senior Engineer Python")

    def test_returns_none_when_pdf_cannot_be_opened(self):
        """A missing or corrupt PDF should return None (not raise) so the
        caller can handle it gracefully."""
        with patch('fitz.open', side_effect=FileNotFoundError("no such file")):
            result = jobsearch.extract_resume_text("missing.pdf")

        self.assertIsNone(result)


class TestHistory(unittest.TestCase):
    """
    load_history / save_history prevent re-scoring jobs across runs.
    These tests verify the persistence contract: what is saved can be reloaded,
    and a missing file is treated as an empty history rather than an error.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history_path = os.path.join(self.tmp.name, 'processed_jobs.json')
        # Point jobsearch at a temp file for isolation.
        self._orig = jobsearch.HISTORY_FILE
        jobsearch.HISTORY_FILE = self.history_path

    def tearDown(self):
        jobsearch.HISTORY_FILE = self._orig
        self.tmp.cleanup()

    def test_load_returns_empty_set_when_no_history_file(self):
        """On a fresh run with no history file, no jobs should be excluded."""
        result = jobsearch.load_history()
        self.assertEqual(result, set())

    def test_save_and_reload_preserves_all_urls(self):
        """URLs saved in one run must be present when loaded in the next run
        so that duplicate scoring is prevented."""
        urls = {"https://example.com/job/1", "https://example.com/job/2"}
        jobsearch.save_history(urls)
        reloaded = jobsearch.load_history()
        self.assertEqual(reloaded, urls)

    def test_save_overwrites_previous_history(self):
        """History is always the full current set — old entries not in the new
        set should not persist (no unbounded file growth)."""
        jobsearch.save_history({"https://old.com/job"})
        new_history = {"https://new.com/job"}
        jobsearch.save_history(new_history)
        reloaded = jobsearch.load_history()
        self.assertEqual(reloaded, new_history)

    def test_history_prevents_duplicate_job_urls(self):
        """Simulates the deduplication logic used in the main pipeline:
        jobs whose URLs are in history should be filtered out."""
        seen_url = "https://example.com/already-scored"
        new_url = "https://example.com/new-job"

        jobsearch.save_history({seen_url})
        history = jobsearch.load_history()

        all_jobs = pd.DataFrame({'job_url': [seen_url, new_url]})
        new_jobs = all_jobs[~all_jobs['job_url'].isin(history)]

        self.assertEqual(len(new_jobs), 1)
        self.assertEqual(new_jobs.iloc[0]['job_url'], new_url)


if __name__ == '__main__':
    unittest.main()
