"""Collect the UAE VAT document list from tax.gov.ae and download the PDFs (R1).

The two FTA listing pages are ASP.NET WebForms grids: a category drop-down, a Search
button and a pager whose links post the form back (__doPostBack). We replay those
form posts with requests + BeautifulSoup; no browser is needed.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from gemini_brain.vat_kb.documents import CORE, Document, classify

log = logging.getLogger("gemini_brain.vat_kb.fta_crawler")

BASE = "https://tax.gov.ae/"
LEGISLATION_PAGE = "https://tax.gov.ae/en/legislation.aspx"
GUIDES_PAGE = "https://tax.gov.ae/en/taxes/Vat/guides.references.aspx"
LEGISLATION_CATEGORIES = ("VAT", "Federal Tax Procedures", "Federal Tax Authority")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
MAX_PAGES = 60


@dataclass
class ListingItem:
    title: str
    category: str
    issue_date: str
    links: list[str] = field(default_factory=list)


class FtaListing:
    """One paginated FTA listing page, optionally filtered to a category."""

    def __init__(self, session: requests.Session, url: str):
        self.session, self.url = session, url

    def items(self, category: str | None = None) -> list[ListingItem]:
        page = self._get()
        dropdown = page.find("select", attrs={"name": re.compile(r"\$ddlCategory$")})
        category_value = None
        if category:
            option = dropdown and next((o for o in dropdown.find_all("option")
                                        if o.get_text(strip=True) == category), None)
            if not option:
                raise RuntimeError(f"FTA category '{category}' not found on {self.url}; page layout changed?")
            category_value = option["value"]
            search = page.find("input", attrs={"name": re.compile(r"\$btnSearch$")})
            form = self._form_state(page)
            form[dropdown["name"]] = category_value
            form[search["name"]] = search.get("value", "Search")
            page = self._post(form)

        found = self._parse(page)
        for _ in range(MAX_PAGES):
            target = self._next_page_target(page)
            if not target:
                break
            form = self._form_state(page)
            form["__EVENTTARGET"], form["__EVENTARGUMENT"] = target, ""
            if category_value is not None:
                form[dropdown["name"]] = category_value
            page = self._post(form)
            batch = self._parse(page)
            if not batch or batch[0].title == found[-len(batch)].title:   # pager looped back
                break
            found.extend(batch)
            time.sleep(0.4)
        return found

    # ── page plumbing ────────────────────────────────────────────────────────
    def _get(self) -> BeautifulSoup:
        return BeautifulSoup(self.session.get(self.url, timeout=60).text, "html.parser")

    def _post(self, form: dict) -> BeautifulSoup:
        return BeautifulSoup(self.session.post(self.url, data=form, timeout=60).text, "html.parser")

    @staticmethod
    def _form_state(page: BeautifulSoup) -> dict:
        state = {}
        for el in page.find("form").find_all(["input", "select", "textarea"]):
            name, kind = el.get("name"), el.get("type", "")
            if not name or kind in ("submit", "button", "image"):
                continue
            if kind in ("checkbox", "radio") and not el.has_attr("checked"):
                continue
            if el.name == "select":
                chosen = el.find("option", selected=True) or el.find("option")
                state[name] = chosen.get("value", "") if chosen else ""
            else:
                state[name] = el.get("value", "")
        return state

    @staticmethod
    def _next_page_target(page: BeautifulSoup) -> str | None:
        link = page.find("a", attrs={"aria-label": "Next page"})
        m = re.search(r"__doPostBack\('([^']+)'", (link.get("href") if link else "") or "")
        return m.group(1) if m else None

    @staticmethod
    def _parse(page: BeautifulSoup) -> list[ListingItem]:
        items = []
        for row in page.select(".commonTableNew > .row"):
            if "headerTable" in (row.get("class") or []):
                continue
            name_cell = row.select_one(".col-md-7 .d-flex") or row.select_one(".col-md-7")
            if name_cell is None:
                continue
            title = " ".join(s.strip() for s in name_cell.find_all(string=True, recursive=False) if s.strip())
            dates = [d.get_text(strip=True) for d in row.select(".lastmodifiedDate")]
            items.append(ListingItem(
                title=re.sub(r"\s+", " ", title),
                category=", ".join(t.get_text(strip=True) for t in row.select(".tag_category")),
                issue_date=dates[0] if dates else "",
                links=[a["href"] for a in row.select("a[href]") if re.search(r"datafolder|\.pdf", a["href"], re.I)],
            ))
        return items


def absolute_url(href: str) -> str:
    url = urljoin(BASE, href.replace("//Datafolder", "/Datafolder").replace("//DataFolder", "/DataFolder"))
    return quote(url, safe=":/?=&%")


def file_name(title: str, url: str, index: int, total: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70]
    suffix = f"-{index + 1}" if total > 1 else ""
    return f"{slug}{suffix}-{hashlib.sha1(url.encode()).hexdigest()[:6]}.pdf"


def collect(session: requests.Session | None = None) -> list[Document]:
    """Crawl both FTA listings and return one Document per unique PDF link, with its tier."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", BROWSER_UA)
    sources = [("legislation", FtaListing(session, LEGISLATION_PAGE), c) for c in LEGISLATION_CATEGORIES]
    sources.append(("guides", FtaListing(session, GUIDES_PAGE), None))

    docs, seen = [], set()
    for source_page, listing, category in sources:
        items = listing.items(category)
        log.info("FTA %s %s: %d items", source_page, category or "(all)", len(items))
        for item in items:
            tier = classify(item.title, item.category, source_page == "legislation")
            for i, href in enumerate(item.links):
                url = absolute_url(href)
                if url.lower() in seen:
                    continue
                seen.add(url.lower())
                docs.append(Document(title=item.title, category=item.category, source_page=source_page,
                                     issue_date=item.issue_date, url=url,
                                     file=file_name(item.title, url, i, len(item.links)), tier=tier))
    return docs


def download(docs: list[Document], pdf_dir: Path, tiers=(CORE,), session: requests.Session | None = None,
             attempts: int = 3) -> list[tuple[Document, str]]:
    """Download missing PDFs for the given tiers. Returns (document, error) for failures."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", BROWSER_UA)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for doc in (d for d in docs if d.tier in tiers):
        target = pdf_dir / doc.file
        if target.exists():
            continue
        error = ""
        for attempt in range(attempts):
            try:
                resp = session.get(doc.url, timeout=120)
                resp.raise_for_status()
                if not resp.content.startswith(b"%PDF"):
                    raise ValueError(f"not a PDF ({resp.headers.get('content-type')})")
                tmp = target.with_suffix(".part")
                tmp.write_bytes(resp.content)
                tmp.replace(target)
                error = ""
                break
            except Exception as e:   # the FTA server drops connections now and then
                error = str(e)
                time.sleep(4 * (attempt + 1))
        if error:
            failures.append((doc, error))
            log.warning("download failed: %s (%s)", doc.title, error)
        time.sleep(0.2)
    return failures
