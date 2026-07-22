"""Orchestration entrypoint."""
import os
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor

from .config import config
from .scraper import fetch_all_item_hrefs, resolve_pdf_download_urls
from .pdf_processor import (
    download_pdf, process_pdf_sync, cleanup, _article_uid_from_download_url
)
from .vector_db import get_index
from .grobid_client import is_alive
from tenacity import RetryError


def _dedupe_items(items: list[dict]) -> list[dict]:
    """Drop items that resolve to the same article (same deterministic
    article_uid). Prevents redundant downloads/processing within a run."""
    seen: set[str] = set()
    unique: list[dict] = []
    for it in items:
        uid = _article_uid_from_download_url(it["download_url"])
        if uid in seen:
            continue
        seen.add(uid)
        unique.append(it)
    return unique

async def _download_and_dispatch(items: list[dict], index):
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    download_sem = asyncio.Semaphore(config.MAX_DOWNLOAD_CONCURRENCY)

    # I/O-bound work (GROBID/Bedrock/S3/Pinecone network calls) -> threads.
    # A ThreadPoolExecutor is correct here because the GIL is released during
    # blocking I/O; a ProcessPoolExecutor would add serialization overhead
    # (the Pinecone `index` object isn't trivially picklable) for no benefit.
    process_pool = ThreadPoolExecutor(max_workers=config.MAX_PROCESS_WORKERS)
    loop = asyncio.get_running_loop()

    processed_count = 0
    total = len(items)
    count_lock = asyncio.Lock()

    async with aiohttp.ClientSession() as session:

        async def handle(item):
            nonlocal processed_count
            article_dir = os.path.join(config.TEMP_DIR, os.urandom(6).hex())
            os.makedirs(article_dir, exist_ok=True)
            # Neutral name: the downloaded bitstream may be a PDF *or* a Word doc. Don't
            # imply ".pdf" — magic-byte detection in document_converter decides the type,
            # and a neutral stem prevents the converted PDF from overwriting the source.
            pdf_path = os.path.join(article_dir, "bitstream")

            async with download_sem:
                ok = await download_pdf(session, item["download_url"], pdf_path)
            if not ok:
                cleanup(article_dir)
                return

            try:
                n = await loop.run_in_executor(
                    process_pool, process_pdf_sync, pdf_path, article_dir, item, index
                )
                if n > 0:
                    async with count_lock:
                        processed_count += 1
                        current = processed_count
                    print(
                        f"[DONE] Finished PDF. "
                        f"Total processed: {current}/{total}"
                    )
            except RetryError as e:
                cause = e.last_attempt.exception()
                print(f"[ERROR] Processing failed for {item['url']}: "
                      f"{type(cause).__name__}: {cause}")
            except Exception as e:  # noqa
                print(f"[ERROR] Processing failed for {item['url']}: {e}")
            finally:
                cleanup(article_dir)

        await asyncio.gather(*(handle(it) for it in items))

    process_pool.shutdown(wait=True)
    return processed_count


async def run():
    print("=" * 60)
    print("[START] Open Science Canada PDF pipeline beginning.")
    print("=" * 60)

    # 1. Pre-flight: verify GROBID is reachable
    if not is_alive():
        print(
            f"[FATAL] GROBID is not reachable at {config.GROBID_URL}.\n"
            f"        Start it with:  docker run -d -p 8070:8070 lfoppiano/grobid:0.8.1\n"
            f"        Then verify:    curl {config.GROBID_URL}/api/isalive\n"
            f"        (Local runs need GROBID_URL=http://localhost:8070, "
            f"not http://grobid:8070.)"
        )
        return

    print("[SCRAPE] Scraping article href links ...")
    item_hrefs = fetch_all_item_hrefs()
    print(f"[SCRAPE] Found {len(item_hrefs)} article href link(s).")

    # 2. Resolve PDF download urls with Playwright workers
    items = await resolve_pdf_download_urls(item_hrefs)

    # De-duplicate by deterministic article_uid so the same PDF is never
    # downloaded or embedded twice within a single run.
    before = len(items)
    items = _dedupe_items(items)
    if len(items) != before:
        print(f"[INFO] Removed {before - len(items)} duplicate article(s).")

    print(f"[INFO] {len(items)} PDF(s) will be processed.")

    if not items:
        print("[END] No PDFs to process. Exiting.")
        return

    # 3. Ensure vector index
    index = get_index()

    # 4. Download + process
    total_processed = await _download_and_dispatch(items, index)

    print("=" * 60)
    print(f"[END] Pipeline finished. {total_processed} PDF(s) processed.")
    print("=" * 60)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()