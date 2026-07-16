"""End-to-end processing of a single PDF article."""
import os
import re
import uuid
import shutil
import aiohttp

from .config import config
from .grobid_client import process_fulltext
from .tei_parser import parse_tei
from .figure_extractor import extract_figure_images
from .storage import upload_figure, upload_thumbnail
from .citation import generate_apa_citation
from .embeddings import embed_text
from .vector_db import article_already_ingested, upsert_vectors


def _article_uid_from_download_url(download_url: str) -> str:
    """Extract bitstream uuid from the /content URL — deterministic id."""
    m = re.search(r"/bitstreams/([0-9a-fA-F-]+)/content", download_url)
    return m.group(1) if m else uuid.uuid5(uuid.NAMESPACE_URL, download_url).hex


async def download_pdf(session: aiohttp.ClientSession, download_url: str,
                       dest_path: str) -> bool:
    try:
        async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=300)) as r:
            if r.status != 200:
                return False
            with open(dest_path, "wb") as f:
                async for chunk in r.content.iter_chunked(1 << 16):
                    f.write(chunk)
        return True
    except Exception as e:  # noqa
        print(f"[DOWNLOAD] Error {download_url}: {e}")
        return False


def process_pdf_sync(pdf_path: str, work_dir: str, item: dict, index) -> int:
    """Blocking CPU/IO heavy work: GROBID -> TEI -> figures -> embeddings.
    Returns number of vectors upserted (0 if skipped)."""
    download_url = item["download_url"]
    page_url = item["url"]
    article_uid = _article_uid_from_download_url(download_url)

    # ---- Dedup check ----
    if article_already_ingested(index, article_uid):
        print(f"[SKIP] Article {article_uid} already ingested.")
        return 0

    # ---- GROBID parse ----
    tei_xml = process_fulltext(pdf_path)
    parsed = parse_tei(tei_xml)
    title = parsed["title"]
    print(f"[PROCESS] Download URL: {item['download_url']}")
    print(f"[PROCESS] Title: {title}")

    # ---- Citation ----
    sample = parsed["sections"][0]["text"] if parsed["sections"] else ""
    citation = generate_apa_citation(title, page_url, sample)

    base_meta = {
        "title": title,
        "citation": citation,
        "url": page_url,
        "download_url": download_url,
        "pdf_thumbnail_url": item.get("pdf_thumbnail_url") or "",
    }

    vectors = []

    # ---- Text sections ----
    for i, sec in enumerate(parsed["sections"]):
        text = f"{sec['heading']}\n{sec['text']}".strip()
        if not text:
            continue
        emb = embed_text(text)
        vectors.append({
            "id": f"{article_uid}-sec-{i}",
            "values": emb,
            "metadata": {**base_meta, "text": text, "section": sec["heading"]},
        })

    # ---- Figures / Tables ----
    fig_dir = os.path.join(work_dir, "figures")
    extracted = extract_figure_images(pdf_path, parsed["figures"], fig_dir)
    for j, ex in enumerate(extracted):
        fig = ex["figure"]
        img_path = ex["image_path"]
        thumb_path = ex.get("thumbnail_path")

        # Upload full-resolution figure
        key = f"{article_uid}/figure_{j}.png"
        image_url = upload_figure(img_path, key)
        # print(f"[S3] Uploaded figure image -> {image_url}")

        # Upload figure thumbnail (135x175 JPEG), if it was created
        image_thumbnail_url = ""
        if thumb_path and os.path.isfile(thumb_path):
            thumb_key = f"{article_uid}/figure_{j}_thumb.jpg"
            image_thumbnail_url = upload_thumbnail(thumb_path, thumb_key)
            # print(f"[S3] Uploaded figure thumbnail -> {image_thumbnail_url}")

        fig_text = fig["text"] or f"{fig['type']} {j}"
        emb = embed_text(fig_text)
        vectors.append({
            "id": f"{article_uid}-fig-{j}",
            "values": emb,
            "metadata": {
                **base_meta,
                "text": fig_text,
                "section": fig["type"],
                "image_url": image_url,
                "image_thumbnail_url": image_thumbnail_url,
            },
        })

    # ---- Upsert ----
    if vectors:
        upsert_vectors(index, vectors, namespace=article_uid)

    return len(vectors)


def cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif p and os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass