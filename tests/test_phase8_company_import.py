import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import backend.companies as companies
import backend.database as database
import backend.services.company_importer as importer
from backend.companies import CompanyUpsert, DuplicateCheckRequest


class FakeResponse:
    def __init__(self, body=b"", status=200, content_type="text/html", headers=None):
        self.body = body
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.encoding = "utf-8"
        self.closed = False

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index : index + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, list):
            return response.pop(0)
        return response


def html(value):
    return FakeResponse(value.encode("utf-8"))


class CompanyImporterTests(unittest.TestCase):
    def test_career_cards_extract_titles_and_external_ats_links_without_fetching_them(self):
        careers = """
        <html><head><title>Careers</title></head><body>
        <nav><h3>Careers</h3></nav>
        <section><h2>Open Positions</h2>
          <article class="job-card">
            <h4>Engineering</h4><h5>AI Engineer</h5>
            <p>On-Site</p><p>Istanbul</p>
            <a href="https://jobs.lever.co/example/ai-123">See Details</a>
          </article>
          <article class="job-card">
            <h4>Engineering</h4><h5>Backend Developer</h5>
            <p>On-Site</p><p>Istanbul</p>
            <a href="https://jobs.lever.co/example/backend-456">See Details</a>
          </article>
          <article class="job-card">
            <h4>Engineering</h4><h5>AI Engineer</h5>
            <a href="https://jobs.lever.co/example/ai-123">Apply Now</a>
          </article>
        </section></body></html>
        """
        session = FakeSession({"https://example.com/careers": html(careers)})
        with patch.object(importer, "_validate_public_host"):
            preview = importer.import_company_preview(
                "https://example.com/careers", session
            )

        self.assertEqual(
            [position["title"] for position in preview["open_positions"]],
            ["AI Engineer", "Backend Developer"],
        )
        self.assertEqual(
            preview["open_positions"][0]["url"],
            "https://jobs.lever.co/example/ai-123",
        )
        self.assertEqual(
            preview["open_positions"][0]["source_url"],
            "https://example.com/careers",
        )
        self.assertEqual(session.calls, ["https://example.com/careers"])
        rejected = {
            "Careers", "Open Positions", "Engineering", "On-Site", "Istanbul",
            "See Details", "Apply Now",
        }
        self.assertTrue(
            rejected.isdisjoint(
                {position["title"] for position in preview["open_positions"]}
            )
        )

    def test_two_urls_in_same_process_and_session_do_not_leak_results(self):
        session = FakeSession(
            {
                "https://a.example/": html(
                    '<html><head><meta property="og:site_name" content="Company A"></head></html>'
                ),
                "https://b.example/": html(
                    '<html><head><meta property="og:site_name" content="Company B"></head></html>'
                ),
            }
        )
        with patch.object(importer, "_validate_public_host"):
            preview_a = importer.import_company_preview("https://a.example/", session)
            preview_b = importer.import_company_preview("https://b.example/", session)

        self.assertEqual(preview_a["website"], "https://a.example/")
        self.assertEqual(preview_a["company_name"], "Company A")
        self.assertEqual(preview_b["website"], "https://b.example/")
        self.assertEqual(preview_b["company_name"], "Company B")
        self.assertEqual(session.calls, ["https://a.example/", "https://b.example/"])

    def test_grounded_extraction_and_shallow_same_domain_crawl(self):
        homepage = """
        <html><head><title>Fallback Name - Home</title>
        <meta property="og:site_name" content="Acme Teknoloji"></head><body>
        <a href="/kariyer">Kariyer</a>
        <a href="/iletisim">İletişim</a>
        <a href="/hakkimizda">Hakkımızda</a>
        <a href="https://external.example/jobs">External jobs</a>
        <p>personal@acme.example</p>
        </body></html>
        """
        careers = """
        <html><head><title>Kariyer</title></head><body>
        <a href="/jobs/ai-engineer">AI Engineer</a>
        <a href="https://ats.example/positions/data-scientist">Data Scientist</a>
        </body></html>
        """
        contact = """
        <html><head><title>İletişim</title></head><body>
        <a href="mailto:info@acme.example">Bilgi</a>
        <p>hr@acme.example</p>
        </body></html>
        """
        about = "<html><head><title>Hakkımızda</title></head><body>Biz kimiz</body></html>"
        session = FakeSession(
            {
                "https://acme.example/": html(homepage),
                "https://acme.example/kariyer": html(careers),
                "https://acme.example/iletisim": html(contact),
                "https://acme.example/hakkimizda": html(about),
            }
        )
        with patch.object(importer, "_validate_public_host"):
            preview = importer.import_company_preview("acme.example", session)

        self.assertEqual(preview["company_name"], "Acme Teknoloji")
        self.assertEqual(preview["contact_email"], "hr@acme.example")
        self.assertEqual(preview["career_page_url"], "https://acme.example/kariyer")
        self.assertEqual(preview["contact_page_url"], "https://acme.example/iletisim")
        self.assertEqual(
            [position["title"] for position in preview["open_positions"]],
            ["AI Engineer", "Data Scientist"],
        )
        self.assertEqual(len(preview["source_pages"]), 4)
        self.assertNotIn("https://external.example/jobs", session.calls)

    def test_company_name_prefers_organization_structured_data(self):
        page = """
        <html><head><title>Title Name</title>
        <meta property="og:site_name" content="OG Name">
        <script type="application/ld+json">
        {"@type":"Organization","name":"Structured Company"}
        </script></head></html>
        """
        session = FakeSession({"https://example.com/": html(page)})
        with patch.object(importer, "_validate_public_host"):
            preview = importer.import_company_preview("https://example.com", session)
        self.assertEqual(preview["company_name"], "Structured Company")

    def test_visible_email_and_mailto_are_extracted_and_role_email_wins(self):
        page = """
        <html><head><title>Example</title></head><body>
        <a href="mailto:contact@example.com?subject=Hello">Contact</a>
        <p>owner@example.com</p><p>careers@example.com</p>
        </body></html>
        """
        session = FakeSession({"https://example.com/": html(page)})
        with patch.object(importer, "_validate_public_host"):
            preview = importer.import_company_preview("https://example.com", session)
        self.assertEqual(preview["contact_email"], "careers@example.com")

    def test_missing_email_and_careers_return_none(self):
        session = FakeSession(
            {"https://example.com/": html("<html><title>Example</title></html>")}
        )
        with patch.object(importer, "_validate_public_host"):
            preview = importer.import_company_preview("https://example.com", session)
        self.assertIsNone(preview["contact_email"])
        self.assertIsNone(preview["career_page_url"])
        self.assertEqual(preview["open_positions"], [])

    def test_maximum_three_internal_pages_are_fetched(self):
        links = "".join(f'<a href="/jobs/{index}">Jobs {index}</a>' for index in range(5))
        responses = {"https://example.com/": html(f"<html><title>Example</title>{links}</html>")}
        responses.update(
            {f"https://example.com/jobs/{index}": html("<html><title>Jobs</title></html>") for index in range(5)}
        )
        session = FakeSession(responses)
        with patch.object(importer, "_validate_public_host"):
            preview = importer.import_company_preview("https://example.com", session)
        self.assertEqual(len(session.calls), 4)
        self.assertEqual(len(preview["source_pages"]), 4)

    def test_cross_domain_redirect_is_rejected(self):
        session = FakeSession(
            {
                "https://example.com/": FakeResponse(
                    status=302,
                    headers={"Location": "https://evil.example/target"},
                )
            }
        )
        with patch.object(importer, "_validate_public_host"):
            with self.assertRaises(importer.CompanyImportError):
                importer.import_company_preview("https://example.com", session)
        self.assertEqual(session.calls, ["https://example.com/"])

    def test_localhost_private_ip_and_invalid_scheme_are_rejected(self):
        with self.assertRaises(importer.CompanyImportError):
            importer.import_company_preview("http://localhost", FakeSession({}))
        with patch.object(
            importer.socket,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 0))],
        ):
            with self.assertRaises(importer.CompanyImportError):
                importer.import_company_preview("http://private.example", FakeSession({}))
        with self.assertRaises(importer.CompanyImportError):
            importer.normalize_url("file:///etc/passwd")
        with self.assertRaises(importer.CompanyImportError):
            importer.normalize_url("ftp://example.com/file")

    def test_oversized_and_non_html_responses_are_rejected(self):
        oversized = FakeResponse(
            b"small", headers={"Content-Length": str(importer.MAX_RESPONSE_BYTES + 1)}
        )
        non_html = FakeResponse(b"PDF", content_type="application/pdf")
        with patch.object(importer, "_validate_public_host"):
            with self.assertRaises(importer.CompanyImportError):
                importer.import_company_preview(
                    "https://large.example",
                    FakeSession({"https://large.example/": oversized}),
                )
            with self.assertRaises(importer.CompanyImportError):
                importer.import_company_preview(
                    "https://file.example",
                    FakeSession({"https://file.example/": non_html}),
                )


class DuplicateCompanyTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "duplicates.db"
        self.database_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.database_patch.start()
        database.initialize_database()
        self.company = companies.create_company(
            CompanyUpsert(
                name="Acme Teknoloji",
                website="https://www.acme.example",
                contact_email="hr@acme.example",
                target_position="AI Engineer",
            )
        )

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_duplicate_detected_by_domain_or_normalized_name(self):
        by_domain = companies.check_company_duplicate(
            DuplicateCheckRequest(name="Different", website="https://acme.example/jobs")
        )
        by_name = companies.check_company_duplicate(
            DuplicateCheckRequest(name="  ACME-teknoloji ", website=None)
        )
        self.assertEqual([item.id for item in by_domain.duplicates], [self.company.id])
        self.assertEqual([item.id for item in by_name.duplicates], [self.company.id])

    def test_preview_endpoint_never_saves_a_company(self):
        before = len(companies.list_companies())
        preview_data = {
            "website": "https://preview.example/",
            "company_name": "Preview Only",
            "contact_email": None,
            "career_page_url": None,
            "contact_page_url": None,
            "open_positions": [],
            "source_pages": ["https://preview.example/"],
        }
        with patch.object(companies, "import_company_preview", return_value=preview_data):
            preview = companies.import_preview(
                companies.CompanyImportRequest(website="https://preview.example")
            )
        after = len(companies.list_companies())
        self.assertEqual(preview.company_name, "Preview Only")
        self.assertEqual(before, after)

    def test_existing_company_crud_remains_available(self):
        listed = companies.list_companies()
        self.assertEqual(len(listed), 1)
        loaded = companies.get_company(self.company.id)
        self.assertEqual(loaded.name, "Acme Teknoloji")
        updated = companies.update_company(
            self.company.id,
            CompanyUpsert(
                name="Acme Updated",
                website="https://acme.example",
                contact_email="jobs@acme.example",
                target_position="Engineer",
            ),
        )
        self.assertEqual(updated.name, "Acme Updated")
        response = companies.delete_company(self.company.id)
        self.assertEqual(response.status_code, 204)
        with self.assertRaises(HTTPException):
            companies.get_company(self.company.id)


if __name__ == "__main__":
    unittest.main()
