"""Orchestration entrypoint."""
import os
import asyncio
import aiohttp
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from .config import config
from .scraper import fetch_all_item_hrefs, resolve_pdf_download_urls
from .pdf_processor import download_pdf, process_pdf_sync, cleanup
from .vector_db import get_index


async def _download_and_dispatch(items: list[dict], index):
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    download_sem = asyncio.Semaphore(config.MAX_DOWNLOAD_CONCURRENCY)
    process_pool = ThreadPoolExecutor(max_workers=config.MAX_PROCESS_WORKERS)
    loop = asyncio.get_running_loop()

    processed_count = 0
    total = len(items)

    async with aiohttp.ClientSession() as session:

        async def handle(item):
            nonlocal processed_count
            article_dir = os.path.join(config.TEMP_DIR, os.urandom(6).hex())
            os.makedirs(article_dir, exist_ok=True)
            pdf_path = os.path.join(article_dir, "article.pdf")

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
                    processed_count += 1
                    print(
                        f"[DONE] Finished PDF. "
                        f"Total processed: {processed_count}/{total}"
                    )
            except Exception as e:  # noqa
                print(f"[ERROR] Processing failed for {item['url']}: {e}")
            finally:
                # Delete PDF + TEI/work artifacts
                cleanup(article_dir)

        await asyncio.gather(*(handle(it) for it in items))

    process_pool.shutdown(wait=True)
    return processed_count


async def run():
    print("=" * 60)
    print("[START] Open Science Canada PDF pipeline beginning.")
    print("=" * 60)

    # 1. Scrape item hrefs
    print("[SCRAPE] Scraping article href links ...")
    item_hrefs = fetch_all_item_hrefs()
    print(f"[SCRAPE] Found {len(item_hrefs)} article href link(s).")

    # 2. Resolve PDF download urls with Playwright workers
    items = await resolve_pdf_download_urls(item_hrefs)
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