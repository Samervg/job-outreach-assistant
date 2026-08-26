import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = "JobOutreachAssistant/1.0 (local website preview)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_INTERNAL_PAGES = 3
MAX_OPEN_POSITIONS = 10
REQUEST_TIMEOUT = (3, 8)

RELEVANT_TERMS = {
    "careers": 100,
    "career": 100,
    "jobs": 100,
    "job": 90,
    "kariyer": 100,
    "iş ilanları": 100,
    "is ilanlari": 100,
    "açık pozisyonlar": 100,
    "acik pozisyonlar": 100,
    "bize katıl": 95,
    "bize katil": 95,
    "join us": 95,
    "work with us": 95,
    "contact": 80,
    "iletişim": 80,
    "iletisim": 80,
    "about": 60,
    "hakkımızda": 60,
    "hakkimizda": 60,
    "team": 50,
    "ekibimiz": 50,
}
CAREER_TERMS = {
    "careers", "career", "jobs", "job", "kariyer", "iş ilanları",
    "is ilanlari", "açık pozisyonlar", "acik pozisyonlar", "bize katıl",
    "bize katil", "join us", "work with us",
}
CONTACT_TERMS = {"contact", "iletişim", "iletisim", "contact us", "bize ulaşın"}
JOB_PATH_TERMS = ("job", "career", "position", "vacanc", "opening", "ilan", "pozisyon")
GENERIC_JOB_LABELS = {
    "apply", "apply now", "başvur", "başvurun", "detay", "details",
    "view", "view job", "learn more", "incele", "devamı", "devami",
    "careers", "career", "jobs", "job", "kariyer", "açık pozisyonlar",
}
JOB_TITLE_TERMS = (
    "engineer", "developer", "artist", "animator", "designer", "manager",
    "intern", "specialist", "analyst", "scientist", "architect", "producer",
    "tester", "recruiter", "consultant", "coordinator", "programmer",
    "muhendis", "gelistirici", "uzman", "stajyer", "tasarimci", "sanatci",
)
NON_JOB_TITLES = GENERIC_JOB_LABELS | {
    "open positions", "open roles", "vacancies", "job openings",
    "engineering", "product", "art", "design", "technology", "operations",
    "marketing", "finance", "human resources", "people", "sales",
    "istanbul", "turkey", "turkiye", "remote", "hybrid", "on-site", "onsite",
    "full-time", "part-time", "contract", "department", "location",
}
DETAIL_LINK_LABELS = {
    "see details", "view details", "job details", "learn more", "apply",
    "apply now", "basvur", "basvurun", "detay", "detaylar", "incele",
}
ATS_HOSTS = ("lever.co", "greenhouse.io", "ashbyhq.com", "workable.com")
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
ROLE_EMAIL_ORDER = (
    "hr", "careers", "jobs", "recruitment", "recruiting", "talent",
    "people", "ik", "insankaynaklari", "info", "contact", "iletisim",
)


class CompanyImportError(Exception):
    pass


@dataclass
class FetchedPage:
    url: str
    html: str


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise CompanyImportError("Web sitesi adresi boş olamaz.")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise CompanyImportError("Yalnızca http ve https adresleri desteklenir.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise CompanyImportError("Web sitesi adresi geçersiz.")
    hostname = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as error:
        raise CompanyImportError("Web sitesi portu geçersiz.") from error
    netloc = hostname if port is None else f"{hostname}:{port}"
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]" if port is None else f"[{hostname}]:{port}"
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _domain_key(hostname: str) -> str:
    hostname = hostname.lower().rstrip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname


def _is_same_domain(url: str, original_domain: str) -> bool:
    hostname = urlparse(url).hostname
    return bool(hostname and _domain_key(hostname) == original_domain)


def _validate_public_host(hostname: str) -> None:
    lowered = hostname.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise CompanyImportError("Yerel veya özel ağ adreslerine erişilemez.")
    try:
        records = socket.getaddrinfo(lowered, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise CompanyImportError("Web sitesi alan adı çözümlenemedi.") from error
    addresses = {record[4][0].split("%")[0] for record in records}
    if not addresses:
        raise CompanyImportError("Web sitesi için IP adresi bulunamadı.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as error:
            raise CompanyImportError("Web sitesi geçersiz bir IP adresine çözümlendi.") from error
        if not ip.is_global:
            raise CompanyImportError("Yerel, özel veya ayrılmış ağ adreslerine erişilemez.")


def _fetch_html(session: requests.Session, url: str, original_domain: str) -> FetchedPage:
    current_url = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current_url)
        if not parsed.hostname or not _is_same_domain(current_url, original_domain):
            raise CompanyImportError("Yönlendirme orijinal alan adının dışına çıktı.")
        _validate_public_host(parsed.hostname)
        try:
            response = session.get(
                current_url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as error:
            raise CompanyImportError("Web sitesine bağlanılamadı.") from error

        if response.status_code in {301, 302, 303, 307, 308}:
            if redirect_count >= MAX_REDIRECTS:
                response.close()
                raise CompanyImportError("Web sitesi çok fazla yönlendirme yaptı.")
            location = response.headers.get("Location")
            if not location:
                response.close()
                raise CompanyImportError("Web sitesi geçersiz bir yönlendirme döndürdü.")
            redirected = normalize_url(urljoin(current_url, location))
            if not _is_same_domain(redirected, original_domain):
                response.close()
                raise CompanyImportError("Yönlendirme orijinal alan adının dışına çıktı.")
            response.close()
            current_url = redirected
            continue

        if response.status_code in {401, 403, 429}:
            response.close()
            raise CompanyImportError("Web sitesi erişimi engelledi; koruma aşılmadı.")
        if response.status_code >= 400:
            response.close()
            raise CompanyImportError(f"Web sitesi HTTP {response.status_code} döndürdü.")

        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            response.close()
            raise CompanyImportError("Web sitesi HTML içerik döndürmedi.")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    response.close()
                    raise CompanyImportError("Web sayfası izin verilen boyuttan büyük.")
            except ValueError:
                pass

        chunks = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise CompanyImportError("Web sayfası izin verilen boyuttan büyük.")
                chunks.append(chunk)
        finally:
            response.close()
        encoding = response.encoding or "utf-8"
        return FetchedPage(current_url, b"".join(chunks).decode(encoding, errors="replace"))
    raise CompanyImportError("Web sitesi yönlendirmesi tamamlanamadı.")


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(unescape(value).split()).strip()
    return cleaned or None


def _extract_company_name(soup: BeautifulSoup) -> str | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            types = entry.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if any(value in {"Organization", "Corporation", "LocalBusiness"} for value in types):
                name = _clean_text(entry.get("name"))
                if name:
                    return name[:200]
    og = soup.select_one('meta[property="og:site_name"]')
    if og:
        name = _clean_text(og.get("content"))
        if name:
            return name[:200]
    if soup.title:
        title = _clean_text(soup.title.get_text(" ", strip=True))
        if title:
            for separator in (" | ", " – ", " — ", " - "):
                if separator in title:
                    title = title.split(separator, 1)[0].strip()
                    break
            if title:
                return title[:200]
    header = soup.select_one("header h1, header [class*=logo], h1")
    name = _clean_text(header.get_text(" ", strip=True)) if header else None
    return name[:200] if name else None


def _extract_emails(soup: BeautifulSoup) -> set[str]:
    emails = set()
    for link in soup.select('a[href^="mailto:"]'):
        address = link.get("href", "")[7:].split("?", 1)[0]
        emails.update(match.lower() for match in EMAIL_PATTERN.findall(address))
    visible_text = soup.get_text(" ", strip=True)
    emails.update(match.lower() for match in EMAIL_PATTERN.findall(visible_text))
    return emails


def _choose_email(emails: set[str]) -> str | None:
    def priority(address: str):
        local = address.split("@", 1)[0].lower().replace("-", "").replace("_", "")
        for index, prefix in enumerate(ROLE_EMAIL_ORDER):
            if local == prefix or local.startswith(prefix):
                return index, address
        return len(ROLE_EMAIL_ORDER), address

    return min(emails, key=priority) if emails else None


def _link_term(text: str, path: str, terms: set[str] | dict[str, int]) -> str | None:
    haystack = f"{text.lower()} {path.lower()}"
    return next((term for term in terms if term in haystack), None)


def _relevant_links(soup: BeautifulSoup, base_url: str, domain: str) -> list[tuple[int, str, str]]:
    found = {}
    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        try:
            absolute = normalize_url(urljoin(base_url, href))
        except CompanyImportError:
            continue
        if not _is_same_domain(absolute, domain):
            continue
        text = _clean_text(link.get_text(" ", strip=True)) or ""
        path = urlparse(absolute).path
        matches = [score for term, score in RELEVANT_TERMS.items() if term in f"{text.lower()} {path.lower()}"]
        if matches:
            found[absolute] = (max(matches), text)
    return sorted(
        [(score, url, text) for url, (score, text) in found.items()],
        key=lambda item: (-item[0], item[1]),
    )


def _is_job_title(value: str | None) -> bool:
    title = _clean_text(value)
    if not title or len(title) < 3 or len(title) > 120:
        return False
    lowered = title.casefold().strip(" :-â€“â€”|")
    if lowered in NON_JOB_TITLES:
        return False
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
        for term in JOB_TITLE_TERMS
    )


def _looks_like_job_link(link, page_url: str) -> bool:
    label = (_clean_text(link.get_text(" ", strip=True)) or "").casefold()
    absolute = urljoin(page_url, link.get("href", "").strip())
    parsed = urlparse(absolute)
    hostname = (parsed.hostname or "").casefold()
    return (
        label in DETAIL_LINK_LABELS
        or any(term in parsed.path.casefold() for term in JOB_PATH_TERMS)
        or any(hostname == host or hostname.endswith(f".{host}") for host in ATS_HOSTS)
    )


def _nearby_job_title(link) -> str | None:
    container = link
    for _ in range(8):
        container = container.parent
        if container is None:
            break
        headings = container.select("h2, h3, h4, h5, h6")
        candidates = [
            _clean_text(heading.get_text(" ", strip=True)) for heading in headings
        ]
        valid = [title for title in candidates if _is_job_title(title)]
        if valid:
            return valid[-1]
    return None


def _extract_jobs(soup: BeautifulSoup, page_url: str) -> list[dict]:
    jobs_by_title = {}

    def add_job(title: str | None, detail_url: str | None = None) -> None:
        title = _clean_text(title)
        if not _is_job_title(title):
            return
        key = title.casefold()
        job = {
            "title": title,
            "url": detail_url or page_url,
            "source_url": page_url,
        }
        existing = jobs_by_title.get(key)
        if existing is None or (
            existing["url"] == page_url and job["url"] != page_url
        ):
            jobs_by_title[key] = job

    # Cards often keep the title in a heading and use a generic "Apply/Details" link.
    for link in soup.select("a[href]"):
        absolute = urljoin(page_url, link.get("href", "").strip())
        link_title = _clean_text(link.get_text(" ", strip=True))
        if _is_job_title(link_title):
            add_job(link_title, absolute)
        elif _looks_like_job_link(link, page_url):
            add_job(_nearby_job_title(link), absolute)

    # Some career pages expose positions only as headings without detail links.
    for heading in soup.select("h2, h3, h4, h5, h6"):
        add_job(heading.get_text(" ", strip=True))

    return list(jobs_by_title.values())[:MAX_OPEN_POSITIONS]


def import_company_preview(website: str, session: requests.Session | None = None) -> dict:
    normalized = normalize_url(website)
    parsed = urlparse(normalized)
    domain = _domain_key(parsed.hostname or "")
    session = session or requests.Session()

    homepage = _fetch_html(session, normalized, domain)
    pages = [homepage]
    homepage_soup = BeautifulSoup(homepage.html, "html.parser")
    relevant = _relevant_links(homepage_soup, homepage.url, domain)
    for _, page_url, _ in relevant[:MAX_INTERNAL_PAGES]:
        try:
            pages.append(_fetch_html(session, page_url, domain))
        except CompanyImportError:
            continue

    emails = set()
    career_page_url = None
    contact_page_url = None
    positions = []
    for page in pages:
        soup = BeautifulSoup(page.html, "html.parser")
        emails.update(_extract_emails(soup))
        path = urlparse(page.url).path
        page_label = _clean_text(soup.title.get_text(" ")) if soup.title else ""
        if career_page_url is None and _link_term(page_label or "", path, CAREER_TERMS):
            career_page_url = page.url
        if contact_page_url is None and _link_term(page_label or "", path, CONTACT_TERMS):
            contact_page_url = page.url
        if _link_term(page_label or "", path, CAREER_TERMS):
            positions.extend(_extract_jobs(soup, page.url))

    for _, link_url, link_text in relevant:
        path = urlparse(link_url).path
        if career_page_url is None and _link_term(link_text, path, CAREER_TERMS):
            career_page_url = link_url
        if contact_page_url is None and _link_term(link_text, path, CONTACT_TERMS):
            contact_page_url = link_url

    unique_positions = []
    seen_positions = set()
    for position in positions:
        key = (position["title"].casefold(), position["url"])
        if key not in seen_positions:
            seen_positions.add(key)
            unique_positions.append(position)

    return {
        "website": homepage.url,
        "company_name": _extract_company_name(homepage_soup),
        "contact_email": _choose_email(emails),
        "career_page_url": career_page_url,
        "contact_page_url": contact_page_url,
        "open_positions": unique_positions[:MAX_OPEN_POSITIONS],
        "source_pages": [page.url for page in pages],
    }
