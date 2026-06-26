"""Pinecone vector database helpers with deduplication support."""
from pinecone import Pinecone, ServerlessSpec
from .config import config

_pc = Pinecone(api_key=config.PINECONE_API_KEY)


def get_index():
    existing = [i["name"] for i in _pc.list_indexes()]
    if config.PINECONE_INDEX_NAME not in existing:
        _pc.create_index(
            name=config.PINECONE_INDEX_NAME,
            dimension=config.PINECONE_DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(
                cloud=config.PINECONE_CLOUD,
                region=config.PINECONE_REGION,
            ),
        )
    return _pc.Index(config.PINECONE_INDEX_NAME)


def article_already_ingested(index, article_uid: str) -> bool:
    """Check whether any vectors for this article already exist.
    We store all vectors of an article under the namespace == article_uid,
    so we just check the namespace stats."""
    try:
        stats = index.describe_index_stats()
        namespaces = stats.get("namespaces", {})
        ns = namespaces.get(article_uid)
        return bool(ns and ns.get("vector_count", 0) > 0)
    except Exception:
        return False


def upsert_vectors(index, vectors: list[dict], namespace: str):
    """vectors: [{'id','values','metadata'}]; batch upsert."""
    BATCH = 100
    for i in range(0, len(vectors), BATCH):
        index.upsert(vectors=vectors[i:i + BATCH], namespace=namespace)