import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.company_import_state import (
    clear_company_import_state,
    clear_stale_company_import_state,
    current_company_import_preview,
    preview_matches_source_url,
    store_company_import_preview,
)


URL_A = "https://www.agito.com.tr/"
URL_B = "https://www.algometatech.com/"
PREVIEW_A = {"company_name": "Agito", "website": URL_A}
PREVIEW_B = {"company_name": "Algometa Tech", "website": URL_B}


class CompanyImportStateTests(unittest.TestCase):
    def test_scan_a_then_change_to_b_hides_old_preview_and_clears_duplicate(self):
        state = {}
        store_company_import_preview(state, URL_A, PREVIEW_A)
        state["company_import_pending"] = {"duplicates": [{"id": 1}]}

        self.assertEqual(current_company_import_preview(state, URL_A), PREVIEW_A)
        clear_stale_company_import_state(state, URL_B)

        self.assertIsNone(current_company_import_preview(state, URL_B))
        self.assertNotIn("company_import_preview", state)
        self.assertNotIn("company_import_pending", state)

    def test_scan_b_stores_and_displays_only_preview_b(self):
        state = {}
        store_company_import_preview(state, URL_B, PREVIEW_B)

        self.assertEqual(current_company_import_preview(state, URL_B), PREVIEW_B)
        self.assertIsNone(current_company_import_preview(state, URL_A))
        self.assertEqual(state["import_preview_source_url"], URL_B)

    def test_failed_new_scan_leaves_no_stale_preview_or_duplicate_state(self):
        state = {
            "company_import_preview": PREVIEW_A,
            "import_preview_source_url": URL_A,
            "company_import_pending": {"duplicates": [{"id": 1}]},
            "import_company_name": "Agito",
        }

        clear_company_import_state(state)
        # A failed request stores nothing after this reset.

        self.assertIsNone(current_company_import_preview(state, URL_B))
        self.assertNotIn("company_import_pending", state)
        self.assertNotIn("import_company_name", state)

    def test_source_url_is_trimmed_consistently(self):
        state = {}
        store_company_import_preview(state, f"  {URL_B}  ", PREVIEW_B)
        self.assertEqual(current_company_import_preview(state, URL_B), PREVIEW_B)

    def test_mismatched_backend_preview_is_rejected(self):
        state = {"company_import_pending": {"duplicates": [{"id": 1}]}}
        stored = store_company_import_preview(state, URL_A, PREVIEW_B)

        self.assertFalse(stored)
        self.assertFalse(preview_matches_source_url(PREVIEW_B, URL_A))
        self.assertIsNone(current_company_import_preview(state, URL_A))
        self.assertNotIn("company_import_pending", state)


class CompanyImportApiClientTests(unittest.TestCase):
    def test_consecutive_urls_send_distinct_json_payloads(self):
        calls = []

        class Response:
            ok = True
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return {
                    "website": self.payload["website"],
                    "company_name": self.payload["website"],
                }

        def fake_post(url, *, json, timeout):
            calls.append((url, dict(json), timeout))
            return Response(json)

        with patch.object(api_client.requests, "post", side_effect=fake_post):
            preview_a, error_a = api_client.import_company_preview(URL_A)
            preview_b, error_b = api_client.import_company_preview(URL_B)

        self.assertIsNone(error_a)
        self.assertIsNone(error_b)
        self.assertEqual(preview_a["website"], URL_A)
        self.assertEqual(preview_b["website"], URL_B)
        self.assertEqual(calls[0][1], {"website": URL_A})
        self.assertEqual(calls[1][1], {"website": URL_B})

    def test_streamlit_form_submits_current_url_for_consecutive_scans(self):
        project_root = Path(__file__).resolve().parents[1]
        frontend = project_root / "frontend"
        previous_directory = Path.cwd()
        calls = []

        def fake_preview(url):
            calls.append(url)
            name = "Agito" if url == URL_A else "Algometa Tech"
            return {
                "website": url,
                "company_name": name,
                "contact_email": None,
                "career_page_url": None,
                "contact_page_url": None,
                "open_positions": [],
                "source_pages": [url],
            }, None

        os.chdir(frontend)
        sys.path.insert(0, str(frontend))
        previous_api_module = sys.modules.get("api_client")
        sys.modules["api_client"] = api_client
        try:
            with patch.object(api_client, "import_company_preview", side_effect=fake_preview), patch.object(
                api_client, "list_companies", return_value=([], None)
            ):
                app = AppTest.from_file(
                    str(frontend / "app_pages" / "companies.py")
                ).run(timeout=20)
                app.text_input[0].set_value(URL_A)
                app.button[0].click().run(timeout=20)
                self.assertEqual(
                    app.session_state["company_import_preview"]["company_name"],
                    "Agito",
                )

                app.text_input[0].set_value(URL_B)
                app.button[0].click().run(timeout=20)
                self.assertEqual(
                    app.session_state["company_import_preview"]["company_name"],
                    "Algometa Tech",
                )
                self.assertEqual(calls, [URL_A, URL_B])
                self.assertEqual(len(app.exception), 0)
        finally:
            os.chdir(previous_directory)
            sys.path.remove(str(frontend))
            if previous_api_module is None:
                sys.modules.pop("api_client", None)
            else:
                sys.modules["api_client"] = previous_api_module


if __name__ == "__main__":
    unittest.main()
