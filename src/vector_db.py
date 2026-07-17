"""Vector database facade.

Selects the backend (Pinecone or Amazon S3 Vectors) based on
config.VECTOR_DB_PROVIDER and exposes a stable, provider-agnostic API:

    get_index()
    article_already_ingested(index, article_uid)
    upsert_vectors(index, vectors, namespace)
"""
from .config import config
from .vector_stores.base import VectorStore


def _build_store() -> VectorStore:
    provider = config.VECTOR_DB_PROVIDER
    if provider == "pinecone":
        from .vector_stores.pinecone_store import PineconeStore
        return PineconeStore()
    if provider in ("s3vectors", "s3-vectors", "s3_vectors"):
        from .vector_stores.s3_vectors_store import S3VectorsStore
        return S3VectorsStore()
    raise ValueError(
        f"Unknown VECTOR_DB_PROVIDER '{provider}'. "
        f"Use 'pinecone' or 's3vectors'."
    )


_store: VectorStore = _build_store()


def get_index():
    return _store.get_index()


def article_already_ingested(index, article_uid: str) -> bool:
    return _store.article_already_ingested(index, article_uid)


def upsert_vectors(index, vectors: list[dict], namespace: str):
    _store.upsert_vectors(index, vectors, namespace)