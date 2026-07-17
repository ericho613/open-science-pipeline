"""End-to-end processing of a single PDF article."""
import os
import re
import uuid
import shutil
import threading
import aiohttp

from .config import config
from .grobid_client import process_fulltext
from .tei_parser import parse_tei
from .figure_extractor import extract_figure_images
from .citation import generate_apa_citation
from .embeddings import embed_text
from .vector_db import article_already_ingested, upsert_vectors
from .storage import upload_figure, upload_thumbnail

# ---- In-process dedup guards (single-run protection) ----
# Prevents two worker threads from processing the same article_uid at the
# same time (the remote "already ingested" check is check-then-act and, on
# S3 Vectors / Pinecone, only eventually consistent). Combined with
# deterministic vector IDs, this guarantees a given article is embedded and
# upserted at most once per run.
_inflight_lock = threading.Lock()
_inflight_uids: set[str] = set()
_completed_uids: set[str] = set()


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


def _claim_article(article_uid: str) -> bool:
    """Atomically claim an article for processing in THIS run.

    Returns True if the caller now owns processing rights for `article_uid`.
    Returns False if it was already completed or is currently in flight in
    another worker thread (i.e. the caller must skip).
    """
    with _inflight_lock:
        if article_uid in _completed_uids or article_uid in _inflight_uids:
            return False
        _inflight_uids.add(article_uid)
        return True


def _release_article(article_uid: str, completed: bool) -> None:
    with _inflight_lock:
        _inflight_uids.discard(article_uid)
        if completed:
            _completed_uids.add(article_uid)


def process_pdf_sync(pdf_path: str, work_dir: str, item: dict, index) -> int:
    """Blocking CPU/IO heavy work: GROBID -> TEI -> figures -> embeddings.
    Returns number of vectors upserted (0 if skipped).

    Dedup guarantee: a given article (identified by its deterministic
    `article_uid`) is embedded + upserted at most once. This holds even under
    concurrent workers and across re-runs, via three layers:
      1. In-process claim (this run, cross-thread).
      2. Remote "already ingested" check (cross-run, best-effort).
      3. Deterministic vector IDs so any re-upsert overwrites in place.

    NOTE: Vectors are created ONLY for TEI text sections. Any figures/tables
    belonging to a section are cropped, uploaded to S3, and their URLs are
    attached to that section's vector via the parallel `image_urls` /
    `image_thumbnail_urls` metadata arrays.
    """
    download_url = item["download_url"]
    page_url = item["url"]
    article_uid = _article_uid_from_download_url(download_url)

    # ---- In-process dedup: claim this article for this run ----
    if not _claim_article(article_uid):
        print(f"[SKIP] Article {article_uid} already claimed/completed this run.")
        return 0

    completed = False
    try:
        # ---- Cross-run dedup check (remote) ----
        if article_already_ingested(index, article_uid):
            print(f"[SKIP] Article {article_uid} already ingested.")
            completed = True  # nothing to do; mark done so we never retry it
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

        # Counts EMITTED vectors (not the raw section index). Empty sections are
        # skipped below, so keying by the section enumerate() index could leave
        # a gap at 0 (e.g. first emitted vector would be sec-1). Using this
        # monotonic counter guarantees the first emitted vector is always
        # '<uid>-sec-0', which the S3 Vectors backend relies on as a strongly
        # consistent "already ingested" sentinel key.
        vec_counter = 0

        # ---- Text sections (the ONLY vectors we create) ----
        for sec in parsed["sections"]:
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
                # Deterministic ID => re-upsert overwrites instead of duplicating.
                # Keyed by emitted-vector counter (not section index) so the first
                # emitted vector is always '<uid>-sec-0'.
                "id": f"{article_uid}-sec-{vec_counter}",
                "values": emb,
                "metadata": {
                    **base_meta,
                    "text": text,
                    "section": heading or "Section",
                    "image_urls": image_urls,
                    "image_thumbnail_urls": image_thumbnail_urls,
                },
            })
            vec_counter += 1

        # ---- Upsert ----
        if vectors:
            upsert_vectors(index, vectors, namespace=article_uid)

        completed = True
        return len(vectors)
    finally:
        # Only mark completed on success (or confirmed already-ingested). If we
        # errored out, release the claim WITHOUT marking complete so a legit
        # retry is still possible; deterministic IDs keep that retry safe.
        _release_article(article_uid, completed=completed)


def cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif p and os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass