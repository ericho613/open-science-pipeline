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
from .citation import generate_apa_citation
from .embeddings import embed_text
from .vector_db import article_already_ingested, upsert_vectors
from .storage import upload_figure, upload_thumbnail


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
    Returns number of vectors upserted (0 if skipped).

    NOTE: Vectors are created ONLY for TEI text sections. Any figures/tables
    belonging to a section are cropped, uploaded to S3, and their URLs are
    attached to that section's vector via the parallel `image_urls` /
    `image_thumbnail_urls` metadata arrays.
    """
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
    print(f"[PROCESS] Title: {title}; download URL: {item['download_url']}")

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

    fig_dir = os.path.join(work_dir, "figures")
    vectors = []

    # Global counter so figure filenames/S3 keys stay unique across sections.
    fig_counter = 0

    # ---- Text sections (the ONLY vectors we create) ----
    for i, sec in enumerate(parsed["sections"]):
        section_figures = sec.get("figures") or []

        # Extract + upload this section's figures -> parallel URL arrays.
        image_urls: list[str] = []
        image_thumbnail_urls: list[str] = []

        if section_figures:
            extracted = extract_figure_images(pdf_path, section_figures, fig_dir)
            for ex in extracted:
                img_path = ex["image_path"]
                thumb_path = ex.get("thumbnail_path")

                # Upload full-resolution figure.
                key = f"{article_uid}/figure_{fig_counter}.png"
                image_url = upload_figure(img_path, key)

                # Upload figure thumbnail (135x175 JPEG); keep arrays aligned.
                thumb_key = f"{article_uid}/figure_{fig_counter}_thumb.jpg"
                if thumb_path and os.path.isfile(thumb_path):
                    image_thumbnail_url = upload_thumbnail(thumb_path, thumb_key)
                else:
                    # No thumbnail produced — store empty string to preserve
                    # positional correspondence between the two arrays.
                    image_thumbnail_url = ""

                image_urls.append(image_url)
                image_thumbnail_urls.append(image_thumbnail_url)
                fig_counter += 1

        # Build the text to embed. Include figure captions so the section
        # vector remains searchable by figure/table content.
        text_parts = []
        heading = sec.get("heading", "").strip()
        body_text = sec.get("text", "").strip()
        if heading:
            text_parts.append(heading)
        if body_text:
            text_parts.append(body_text)
        for fig in section_figures:
            fig_caption = (fig.get("text") or "").strip()
            if fig_caption:
                text_parts.append(fig_caption)
        text = "\n".join(text_parts).strip()

        # Skip empty sections that also produced no figures — nothing to embed.
        if not text and not image_urls:
            continue

        # If there's no text at all but there ARE figures, embed the captions
        # (already folded into `text` above); if still empty, fall back to title.
        embed_input = text or title

        emb = embed_text(embed_input)
        vectors.append({
            "id": f"{article_uid}-sec-{i}",
            "values": emb,
            "metadata": {
                **base_meta,
                "text": text,
                "section": heading or "Section",
                "image_urls": image_urls,
                "image_thumbnail_urls": image_thumbnail_urls,
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