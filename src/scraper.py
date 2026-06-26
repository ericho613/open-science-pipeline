"""Scrape article item hrefs from the sitemap, then resolve PDF download URLs
using a pool of Playwright workers."""
import asyncio
import math
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .config import config


def fetch_all_item_hrefs() -> list[str]:
    """Scrape every /items/<uuid> href from the sitemap index + child sitemaps."""
    print("[SCRAPE] Fetching sitemap index ...")
    parent_resp = requests.get(f"{config.FOSRC_SERVER_LINK}/sitemap_index.html", timeout=60)
    parent_soup = BeautifulSoup(parent_resp.content, "html.parser")

    sitemap_parents = [
        a.get("href") for a in parent_soup.find_all("a") if a.get("href")
    ]

    item_hrefs: list[str] = []
    for href in sitemap_parents:
        try:
            child_resp = requests.get(href, timeout=60)
        except Exception as e:  # noqa
            print(f"[SCRAPE] Failed child sitemap {href}: {e}")
            continue
        child_soup = BeautifulSoup(child_resp.content, "html.parser")
        for a in child_soup.find_all("a"):
            h = a.get("href")
            if h and h.startswith(f"{config.FOSRC_SERVER_LINK}/items"):
                item_hrefs.append(h)

    # De-duplicate while preserving order
    seen = set()
    unique = []
    for h in item_hrefs:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def _relative_to_download_url(relative_href: str) -> str | None:
    """Convert '/bitstreams/<uuid>/download' -> absolute /content URL."""
    if not relative_href:
        return None
    trimmed = relative_href.rsplit("/download", 1)[0]
    return f"{config.FOSRC_SERVER_LINK}/server/api/core{trimmed}/content"


def _compute_worker_count(num_targets: int) -> int:
    """Scale workers: +1 per PDFS_PER_WORKER PDFs, capped by MAX."""
    workers = max(1, math.ceil(num_targets / config.PDFS_PER_WORKER))
    return min(workers, config.MAX_PLAYWRIGHT_WORKERS)


async def _resolve_pdf_url(page, item_url: str) -> dict | None:
    """Navigate to an item page and extract the download url."""
    try:
        await page.goto(item_url, wait_until="networkidle", timeout=60000)
        # Wait for the download-link component to appear
        await page.wait_for_selector("ds-file-download-link a", timeout=30000)
        anchor = await page.query_selector("ds-file-download-link a")
        if not anchor:
            return None
        relative = await anchor.get_attribute("href")
        download_url = _relative_to_download_url(relative)
        if not download_url:
            return None
        return {"url": item_url, "download_url": download_url}
    except Exception as e:  # noqa
        print(f"[SCRAPE] Could not resolve {item_url}: {e}")
        return None


async def _worker(browser, chunk: list[str], results: list, lock: asyncio.Lock,
                  limit: int | None):
    context = await browser.new_context()
    page = await context.new_page()
    try:
        for item_url in chunk:
            # Stop early if the global limit was reached
            async with lock:
                if limit is not None and len(results) >= limit:
                    break
            resolved = await _resolve_pdf_url(page, item_url)
            if resolved:
                async with lock:
                    if limit is None or len(results) < limit:
                        results.append(resolved)
    finally:
        await page.close()
        await context.close()


async def resolve_pdf_download_urls(item_hrefs: list[str]) -> list[dict]:
    """Use a scaled pool of Playwright workers to resolve PDF download URLs."""
    limit = config.NUM_PDFS_TO_PROCESS
    # If limited, we don't need to visit ALL item pages; but pages may fail to
    # resolve, so we keep the full list and stop once the limit is met.
    targets = item_hrefs
    num_targets = limit if limit is not None else len(targets)

    worker_count = _compute_worker_count(num_targets)
    print(f"[SCRAPE] Spawning {worker_count} Playwright worker(s).")

    # Split targets into `worker_count` contiguous chunks (no overlap).
    chunk_size = math.ceil(len(targets) / worker_count)
    chunks = [targets[i:i + chunk_size] for i in range(0, len(targets), chunk_size)]

    results: list[dict] = []
    lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        tasks = [
            _worker(browser, chunk, results, lock, limit)
            for chunk in chunks
        ]
        await asyncio.gather(*tasks)
        await browser.close()

    if limit is not None:
        results = results[:limit]

    print(f"[SCRAPE] Resolved {len(results)} PDF download URL(s).")
    return results