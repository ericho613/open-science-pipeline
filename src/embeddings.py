"""Create text embeddings via Amazon Bedrock Titan."""
import json
import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import config

_bedrock = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=15))
def embed_text(text: str) -> list[float]:
    body = {
        "inputText": text[:8000],  # Titan token limits; truncate safely
        "dimensions": config.PINECONE_DIMENSION,
        "normalize": True,
    }
    resp = _bedrock.invoke_model(
        modelId=config.EMBEDDING_MODEL,
        body=json.dumps(body),
    )
    payload = json.loads(resp["body"].read())
    return payload["embedding"]