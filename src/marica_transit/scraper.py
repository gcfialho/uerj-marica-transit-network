"""Moovit scraper for EPT (Maricá) bus lines and their ordered stop lists.

Network access is **opt-in** (``--force-rescrape``); the default pipeline reads the
committed snapshot in ``data/raw``. Check Moovit's terms of service before re-scraping.
Requires the ``fetch`` extra: ``uv sync --extra fetch``.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import pandas as pd

if TYPE_CHECKING:
    from bs4 import Tag

log = logging.getLogger(__name__)

BASE_URL = "https://moovitapp.com"
LINE_LINK_SELECTORS = (
    'ul.lines-list li a[href*="/transporte_p%C3%BAblico-line-"]',
    'ul.agency-lines-group li a[href*="/transporte_p%C3%BAblico-line-"]',
    "div.lines_container a.line-item",
    "a.line-item",
)
LINE_CODE_RE = re.compile(r"line-([a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*)-Rio_de_Janeiro")


def require_fetch_extra() -> tuple[Any, Any]:
    try:
        import requests  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "Scraping and geocoding need the optional 'fetch' dependencies: uv sync --extra fetch"
        ) from exc
    return requests, BeautifulSoup


def _squash(text: str) -> str:
    return " ".join(text.split())


class MoovitScraper:
    """Fetches the EPT line index and each line page, with simple retries."""

    def __init__(
        self,
        pause_s: float = 2.5,
        retries: int = 3,
        retry_delay_s: float = 5,
        timeout_s: float = 20,
    ) -> None:
        self._requests, self._soup = require_fetch_extra()
        self.pause_s = pause_s
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self.timeout_s = timeout_s
        self.headers = {"User-Agent": "marica-transit research scraper (+academic use)"}

    def fetch_html(self, url: str) -> str | None:
        for attempt in range(1, self.retries + 1):
            try:
                resp = self._requests.get(url, headers=self.headers, timeout=self.timeout_s)
                resp.raise_for_status()
                return resp.text
            except self._requests.RequestException as exc:
                log.warning("attempt %d/%d failed for %s: %s", attempt, self.retries, url, exc)
            if attempt < self.retries:
                time.sleep(self.retry_delay_s * attempt)
        log.error("giving up on %s", url)
        return None

    def extract_line_links(self, html_text: str) -> list[dict[str, str | None]]:
        """Parse the line index into ``{line_code, line_name, url}`` dicts (deduplicated)."""
        soup = self._soup(html_text, "html.parser")
        anchors: list[Tag] = []
        for selector in LINE_LINK_SELECTORS:
            anchors = soup.select(selector)
            if anchors:
                log.debug("line links matched selector %r", selector)
                break
        lines: dict[str, dict[str, str | None]] = {}
        for a in anchors:
            href = a.get("href")
            if isinstance(href, list):
                href = href[0] if href else None
            if not href:
                continue
            url = urljoin(BASE_URL, href)
            match = LINE_CODE_RE.search(url)
            code = match.group(1).upper() if match else None
            name = self._line_name(a, code)
            key = code or name
            if key and key not in lines:
                lines[key] = {"line_code": code, "line_name": name, "url": url}
        return [lines[k] for k in sorted(lines)]

    @staticmethod
    def _line_name(anchor: Tag, code: str | None) -> str | None:
        for scope in (anchor, anchor.parent):
            if scope is None:
                continue
            title = scope.select_one("div.line-title h2.title")
            if title and (text := _squash(title.get_text(" ", strip=True))):
                return text
        for span in anchor.find_all("span"):
            text = _squash(span.get_text(" ", strip=True))
            if text and (code is None or text.lower() != code.lower()):
                return text
        return None

    def extract_stops(self, html_text: str, line: dict[str, str | None]) -> list[dict[str, Any]]:
        """Parse one line page into ordered stop rows, one block per direction."""
        soup = self._soup(html_text, "html.parser")
        rows: list[dict[str, Any]] = []
        for wrapper in soup.find_all("div", class_="stops-wrapper"):
            header = wrapper.select_one("div.stops-header h2")
            direction = "Unknown direction"
            if header:
                text = header.get_text(strip=True)
                text = text.split("Sentido: ", 1)[1] if "Sentido: " in text else text
                direction = text.split("(", 1)[0].strip() or direction
            stop_list = wrapper.select_one("ul[class*='stops-list']")
            if stop_list is None:
                continue
            order = 1
            for li in stop_list.find_all("li", class_="stop-container"):
                h3 = li.select_one("div.stop-wrapper h3")
                name = h3.get_text(strip=True) if h3 else ""
                if not name:
                    continue
                rows.append(
                    {
                        "line_code": line["line_code"],
                        "line_name": line["line_name"],
                        "line_url": line["url"],
                        "direction": direction,
                        "stop_order": order,
                        "stop_name": name,
                    }
                )
                order += 1
        return rows

    def scrape(self, index_url: str, limit: int | None = None) -> pd.DataFrame:
        """Scrape every line listed on ``index_url`` into a raw stops DataFrame."""
        index_html = self.fetch_html(index_url)
        if not index_html:
            raise RuntimeError(f"could not fetch the line index {index_url}")
        lines = self.extract_line_links(index_html)[:limit]
        if not lines:
            raise RuntimeError("no lines found; Moovit's markup may have changed")
        log.info("found %d lines", len(lines))
        rows: list[dict[str, Any]] = []
        for i, line in enumerate(lines, 1):
            page = self.fetch_html(str(line["url"]))
            stops = self.extract_stops(page, line) if page else []
            log.info("[%d/%d] %s: %d stops", i, len(lines), line["line_code"], len(stops))
            rows.extend(stops)
            if i < len(lines):
                time.sleep(self.pause_s)
        if not rows:
            raise RuntimeError("no stops scraped")
        return pd.DataFrame(rows)
