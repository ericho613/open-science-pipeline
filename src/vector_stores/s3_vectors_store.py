"""Amazon S3 Vectors backend.

S3 Vectors has no concept of Pinecone-style namespaces. We therefore:
  - store `article_uid` inside each vector's metadata,
  - prefix each vector key with the uid (`<uid>::<id>`), and
  - detect duplicates via a strongly-consistent key lookup of the first
    section vector (`<uid>::<uid>-sec-0`).
"""
import boto3

from ..config import config
from .base import VectorStore

# S3 Vectors metadata values must be strings/numbers/bools or lists of strings.
# Some pipeline metadata (image_urls, image_thumbnail_urls) are already lists
# of strings, which is supported. Long free-text ("text") is kept but note that
# S3 Vectors caps filterable metadata size — we mark large fields non-filterable.
_NON_FILTERABLE_KEYS = ["text", "citation", "image_urls", "image_thumbnail_urls"]


class S3VectorsStore(VectorStore):
    def __init__(self):
        self._client = boto3.client(
            "s3vectors", region_name=config.S3_VECTORS_REGION
        )
        self._bucket = config.S3_VECTORS_BUCKET
        self._index = config.S3_VECTORS_INDEX_NAME

    def get_index(self):
        if not self._bucket:
            raise ValueError(
                "S3_VECTORS_BUCKET must be set when VECTOR_DB_PROVIDER=s3vectors."
            )
        self._ensure_bucket()
        self._ensure_index()
        # The "handle" is just the (bucket, index) identity; return self so the
        # module-level functions can delegate. We return a lightweight dict.
        return {"bucket": self._bucket, "index": self._index}

    def _ensure_bucket(self):
        try:
            self._client.get_vector_bucket(vectorBucketName=self._bucket)
        except self._client.exceptions.NotFoundException:
            self._client.create_vector_bucket(vectorBucketName=self._bucket)

    def _ensure_index(self):
        try:
            self._client.get_index(
                vectorBucketName=self._bucket, indexName=self._index
            )
        except self._client.exceptions.NotFoundException:
            self._client.create_index(
                vectorBucketName=self._bucket,
                indexName=self._index,
                dataType="float32",
                dimension=config.VECTOR_DIMENSION,
                distanceMetric=config.S3_VECTORS_DISTANCE_METRIC,
                metadataConfiguration={
                    "nonFilterableMetadataKeys": _NON_FILTERABLE_KEYS,
                },
            )

    @staticmethod
    def _vector_key(article_uid: str, vector_id: str) -> str:
        """Deterministic on-disk key for a vector: '<uid>::<id>'."""
        return f"{article_uid}::{vector_id}"

    def article_already_ingested(self, handle, article_uid: str) -> bool:
        """Return True if this article's vectors already exist.

        We do a STRONGLY-CONSISTENT key lookup instead of a similarity query:
        every article writes at least one section vector whose id is
        '<uid>-sec-0', stored under key '<uid>::<uid>-sec-0'. If that key
        exists, the article has already been ingested.

        This avoids the pitfalls of the previous query-based approach:
          - no degenerate zero query-vector (undefined under cosine),
          - not subject to query eventual-consistency lag,
          - no dependence on filterable-metadata behaviour.
        """
        sentinel_key = self._vector_key(article_uid, f"{article_uid}-sec-0")
        try:
            resp = self._client.get_vectors(
                vectorBucketName=handle["bucket"],
                indexName=handle["index"],
                keys=[sentinel_key],
                returnData=False,
                returnMetadata=False,
            )
            return len(resp.get("vectors", [])) > 0
        except self._client.exceptions.NotFoundException:
            # Index/key not found -> not ingested.
            return False
        except Exception:
            # On any unexpected error, fail "not ingested" so we don't silently
            # skip an article that actually needs processing. Deterministic
            # vector keys make a redundant re-upsert safe (it overwrites).
            return False

    def upsert_vectors(self, handle, vectors: list[dict], namespace: str) -> None:
        """Batch put vectors. `namespace` == article_uid; we fold it into
        metadata and prefix keys for uniqueness."""
        BATCH = 100  # S3 Vectors PutVectors accepts up to 500; stay conservative.
        prepared = [self._to_s3_vector(v, namespace) for v in vectors]
        for i in range(0, len(prepared), BATCH):
            self._client.put_vectors(
                vectorBucketName=handle["bucket"],
                indexName=handle["index"],
                vectors=prepared[i:i + BATCH],
            )

    @staticmethod
    def _to_s3_vector(vector: dict, article_uid: str) -> dict:
        metadata = dict(vector.get("metadata") or {})
        metadata["article_uid"] = article_uid
        return {
            "key": S3VectorsStore._vector_key(article_uid, vector["id"]),
            "data": {"float32": vector["values"]},
            "metadata": S3VectorsStore._clean_metadata(metadata),
        }

    @staticmethod
    def _clean_metadata(metadata: dict) -> dict:
        """S3 Vectors rejects empty arrays (and None) in metadata. Strip any
        empty/None values so PutVectors doesn't raise a ValidationException.
        Empty lists carry no information anyway, so dropping them is safe."""
        cleaned = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, str, dict)) and len(value) == 0:
                continue
            cleaned[key] = value
        return cleaned