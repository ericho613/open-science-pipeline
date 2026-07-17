"""Abstract vector store interface.

All backends expose the same 4 operations used by the pipeline:
    - get_index()                              -> opaque handle
    - article_already_ingested(handle, uid)    -> bool
    - upsert_vectors(handle, vectors, namespace)
    - (optional) provider-specific setup handled internally

`vectors` is always a list of {'id', 'values', 'metadata'} dicts.
`namespace` is the article_uid (used for per-article grouping / dedup).
"""
from abc import ABC, abstractmethod


class VectorStore(ABC):
    @abstractmethod
    def get_index(self):
        """Ensure the index exists and return an opaque handle."""

    @abstractmethod
    def article_already_ingested(self, handle, article_uid: str) -> bool:
        """Return True if this article's vectors already exist."""

    @abstractmethod
    def upsert_vectors(self, handle, vectors: list[dict], namespace: str) -> None:
        """Batch upsert vectors under the given namespace (article_uid)."""