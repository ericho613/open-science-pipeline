"""Pinecone vector store backend."""
from pinecone import Pinecone, ServerlessSpec

from ..config import config
from .base import VectorStore


class PineconeStore(VectorStore):
    def __init__(self):
        self._pc = Pinecone(api_key=config.PINECONE_API_KEY)

    def get_index(self):
        existing = [i["name"] for i in self._pc.list_indexes()]
        if config.PINECONE_INDEX_NAME not in existing:
            self._pc.create_index(
                name=config.PINECONE_INDEX_NAME,
                dimension=config.VECTOR_DIMENSION,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud=config.PINECONE_CLOUD,
                    region=config.PINECONE_REGION,
                ),
            )
        return self._pc.Index(config.PINECONE_INDEX_NAME)

    def article_already_ingested(self, handle, article_uid: str) -> bool:
        """All vectors of an article live under namespace == article_uid,
        so we just check the namespace stats."""
        try:
            stats = handle.describe_index_stats()
            namespaces = stats.get("namespaces", {})
            ns = namespaces.get(article_uid)
            return bool(ns and ns.get("vector_count", 0) > 0)
        except Exception:
            return False

    def upsert_vectors(self, handle, vectors: list[dict], namespace: str) -> None:
        BATCH = 100
        for i in range(0, len(vectors), BATCH):
            handle.upsert(vectors=vectors[i:i + BATCH], namespace=namespace)