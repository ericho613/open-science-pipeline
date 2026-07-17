"""Centralized configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_int(key: str, default: int | None) -> int | None:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return int(val)


class Config:
    # Source
    FOSRC_SERVER_LINK = os.getenv("FOSRC_SERVER_LINK", "https://open-science.canada.ca")

    # Number of PDFs (None == all)
    NUM_PDFS_TO_PROCESS = _get_int("NUM_PDFS_TO_PROCESS", None)

    # Playwright
    MAX_PLAYWRIGHT_WORKERS = _get_int("MAX_PLAYWRIGHT_WORKERS", 5)
    PDFS_PER_WORKER = _get_int("PDFS_PER_WORKER", 100)

    # GROBID
    GROBID_URL = os.getenv("GROBID_URL", "http://localhost:8070")

    # AWS / Bedrock
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
    CITATION_MODEL = os.getenv(
        "CITATION_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )

    # S3 (figure/thumbnail image storage)
    S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
    S3_PUBLIC_BASE_URL = os.getenv("S3_PUBLIC_BASE_URL", "")

    # ---- Vector DB provider selection ----
    # "pinecone" or "s3vectors"
    VECTOR_DB_PROVIDER = os.getenv("VECTOR_DB_PROVIDER", "pinecone").strip().lower()

    # Shared: dimension of the embedding vectors
    VECTOR_DIMENSION = _get_int("VECTOR_DIMENSION", 1024)

    # Pinecone
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "open-science-articles")
    PINECONE_CLOUD = os.getenv("PINECONE_CLOUD", "aws")
    PINECONE_REGION = os.getenv("PINECONE_REGION", "us-east-1")

    # Amazon S3 Vectors
    S3_VECTORS_BUCKET = os.getenv("S3_VECTORS_BUCKET")
    S3_VECTORS_INDEX_NAME = os.getenv(
        "S3_VECTORS_INDEX_NAME", "open-science-articles"
    )
    # cosine | euclidean
    S3_VECTORS_DISTANCE_METRIC = os.getenv(
        "S3_VECTORS_DISTANCE_METRIC", "cosine"
    )
    S3_VECTORS_REGION = os.getenv("S3_VECTORS_REGION", AWS_REGION)

    # Concurrency
    MAX_DOWNLOAD_CONCURRENCY = _get_int("MAX_DOWNLOAD_CONCURRENCY", 5)
    MAX_PROCESS_WORKERS = _get_int("MAX_PROCESS_WORKERS", 4)

    # Temp
    TEMP_DIR = os.getenv("TEMP_DIR", "./tmp")


config = Config()