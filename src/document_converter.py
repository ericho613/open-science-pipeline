"""Detect and convert Word documents (.doc/.docx) to PDF before GROBID.

GROBID only accepts PDFs, so any Word document must be converted first. We
detect the true file type by inspecting magic bytes (not just the extension,
which is unreliable for downloaded bitstreams) and convert via LibreOffice in
headless mode.

Concurrency: headless LibreOffice is not safe to run concurrently against a
shared user profile — parallel `soffice` processes sharing ~/.config/libreoffice
can deadlock or fail with profile-lock errors. We guard against this two ways:
  1. Each invocation gets its own isolated, throwaway `-env:UserInstallation`
     profile directory (avoids lock contention entirely).
  2. A module-level semaphore bounds how many conversions run at once
     (LibreOffice is memory-hungry; unbounded parallelism can OOM the host).

Requires LibreOffice (`soffice`) to be installed and on PATH:
    - Debian/Ubuntu:  apt-get install -y libreoffice
    - macOS (brew):   brew install --cask libreoffice
"""
import os
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path

from .config import config


# Magic-byte signatures.
#   .docx (and all OOXML) are ZIP archives -> start with 'PK\x03\x04'.
#   .doc (legacy OLE2 Compound File) -> 'D0 CF 11 E0 A1 B1 1A E1'.
_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PDF_MAGIC = b"%PDF"

# Bound concurrent LibreOffice conversions. Even with isolated profiles,
# soffice is memory-heavy, so we cap parallelism independently of the pipeline's
# thread-pool size. Defaults to 2 but is configurable via env.
_conversion_sem = threading.BoundedSemaphore(config.MAX_LIBREOFFICE_WORKERS)


def _read_magic(path: str, n: int = 8) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def is_pdf(path: str) -> bool:
    return _read_magic(path, 4) == _PDF_MAGIC


def is_word_document(path: str) -> bool:
    """Return True if the file is a .doc (OLE2) or .docx (OOXML/ZIP) document.

    Detection is by magic bytes first, falling back to the file extension. Note
    that a raw ZIP signature also matches non-Word OOXML (xlsx/pptx); we treat
    a ZIP as a Word document unless the extension explicitly says otherwise.
    """
    ext = os.path.splitext(path)[1].lower()
    magic = _read_magic(path)

    if ext == ".docx" and magic.startswith(_ZIP_MAGIC):
        return True
    if ext == ".doc" and magic.startswith(_OLE2_MAGIC):
        return True

    # Extension missing/misleading (e.g. downloaded bitstreams saved as
    # "article.pdf"): infer from magic bytes.
    if magic.startswith(_OLE2_MAGIC):
        # Legacy Office (could be .doc/.xls/.ppt). Treat as .doc for conversion;
        # LibreOffice will still produce a PDF for any of them.
        return True
    if magic.startswith(_ZIP_MAGIC) and ext not in (".xlsx", ".pptx", ".zip"):
        # OOXML/ZIP. Treat as .docx for conversion unless the extension clearly
        # indicates a non-Word Office type. LibreOffice still produces a PDF for
        # xlsx/pptx, so this is safe even if the guess is imperfect.
        return True

    return False


def convert_to_pdf(src_path: str, out_dir: str) -> str:
    """Convert a Word document to PDF using headless LibreOffice.

    Returns the path to the generated PDF. Raises RuntimeError on failure so
    the caller's retry/error handling in the pipeline kicks in.

    Thread-safe: bounded by a semaphore, and each run uses an isolated
    LibreOffice user-profile directory so concurrent conversions never contend
    on the shared default profile lock.

    All paths are made absolute before invoking LibreOffice: `as_uri()` requires
    an absolute path (config.TEMP_DIR defaults to the relative "./tmp"), and
    LibreOffice behaves inconsistently with relative --outdir / input paths.

    The converted PDF is written into an isolated subdirectory of `out_dir` so
    it can never overwrite the source bitstream (which may share a stem).
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice ('soffice') not found on PATH. Install it to enable "
            "DOC/DOCX -> PDF conversion (e.g. `apt-get install -y libreoffice`)."
        )

    os.makedirs(out_dir, exist_ok=True)
    # Make paths absolute: as_uri() requires an absolute path, and absolute
    # --outdir / input paths avoid LibreOffice's inconsistent handling of
    # relative paths (its working directory isn't guaranteed).
    out_dir = os.path.abspath(out_dir)
    src_path = os.path.abspath(src_path)

    # Convert into a dedicated subdirectory so the output PDF can never collide
    # with (or overwrite) the source bitstream, even if they share a stem.
    convert_out_dir = os.path.join(out_dir, "converted")
    os.makedirs(convert_out_dir, exist_ok=True)

    with _conversion_sem:
        # Isolated, throwaway user profile for THIS conversion. Prevents the
        # "profile is already in use" lock error and profile corruption that
        # occurs when multiple headless soffice processes share ~/.config.
        with tempfile.TemporaryDirectory(
            prefix="lo_profile_", dir=out_dir
        ) as profile_dir:
            # LibreOffice wants a file:// URI for the UserInstallation path.
            # out_dir is now absolute so profile_dir (created inside it) is too;
            # resolve() is kept as a belt-and-suspenders guard against symlinks
            # and '..' segments.
            profile_uri = Path(profile_dir).resolve().as_uri()
            cmd = [
                soffice,
                "--headless",
                "--nologo",
                "--nolockcheck",
                "--nodefault",
                "--norestore",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to", "pdf",
                "--outdir", convert_out_dir,
                src_path,
            ]
            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"LibreOffice conversion timed out: {e}") from e
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"LibreOffice conversion failed (exit {e.returncode}): "
                    f"{e.stderr.decode(errors='replace')}"
                ) from e

    # LibreOffice names the output '<source-stem>.pdf' in --outdir.
    stem = os.path.splitext(os.path.basename(src_path))[0]
    pdf_path = os.path.join(convert_out_dir, f"{stem}.pdf")
    if not os.path.isfile(pdf_path):
        raise RuntimeError(
            f"LibreOffice reported success but no PDF was produced at "
            f"{pdf_path}. stdout={result.stdout.decode(errors='replace')}"
        )
    return pdf_path


def ensure_pdf(path: str, work_dir: str) -> str:
    """Return a path to a PDF version of `path`.

    - If it's already a PDF, return it unchanged.
    - If it's a Word document, convert it and return the new PDF path.
    - Otherwise, return it unchanged and let GROBID reject it downstream.
    """
    if is_pdf(path):
        return path
    if is_word_document(path):
        pdf_path = convert_to_pdf(path, work_dir)
        print(f"[CONVERT] Converted Word document -> {os.path.basename(pdf_path)}")
        return pdf_path
    return path