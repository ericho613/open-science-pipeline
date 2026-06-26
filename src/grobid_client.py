"""Thin wrapper around the GROBID processFulltextDocument API."""
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import config


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
def process_fulltext(pdf_path: str) -> str:
    """Send a PDF to GROBID and return TEI-XML with coordinates for figures/tables."""
    url = f"{config.GROBID_URL}/api/processFulltextDocument"
    with open(pdf_path, "rb") as f:
        files = {"input": (pdf_path, f, "application/pdf")}
        data = {
            # Request coordinates so we can crop figures/tables.
            "teiCoordinates": ["figure", "table"],
            "consolidateHeader": "1",
        }
        resp = requests.post(url, files=files, data=data, timeout=300)
    resp.raise_for_status()
    return resp.text