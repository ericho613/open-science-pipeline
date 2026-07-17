"""Amazon S3 Vectors backend.

S3 Vectors has no concept of Pinecone-style namespaces. We therefore:
  - store `article_uid` inside each vector's metadata,
  - prefix each vector key with the uid (`<uid>::<id>`), and
  - detect duplicates via a metadata-filtered query.
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

    def article_already_ingested(self, handle, article_uid: str) -> bool:
        """Query with a metadata filter on article_uid; if any vector comes
        back, the article is already ingested."""
        try:
            # We need a query vector; zero vector is fine since we only care
            # whether the filter matches anything (topK=1).
            zero = [0.0] * config.VECTOR_DIMENSION
            resp = self._client.query_vectors(
                vectorBucketName=handle["bucket"],
                indexName=handle["index"],
                topK=1,
                queryVector={"float32": zero},
                filter={"article_uid": article_uid},
                returnMetadata=False,
                returnDistance=False,
            )
            return len(resp.get("vectors", [])) > 0
        except Exception:
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
            "key": f"{article_uid}::{vector['id']}",
            "data": {"float32": vector["values"]},
            "metadata": metadata,
        }