"""Create text embeddings via Amazon Bedrock Titan."""
import json
import boto3
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .config import config
from botocore.exceptions import ClientError

_bedrock = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)

_VALID_TITAN_DIMS = {256, 512, 1024}

def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        # Retry only throttling / transient service errors
        return code in {"ThrottlingException", "ServiceUnavailableException",
                        "ModelTimeoutException", "InternalServerException"}
    return False

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=15),
    retry=retry_if_exception(_is_transient),
)
def embed_text(text: str) -> list[float]:
    if config.VECTOR_DIMENSION not in _VALID_TITAN_DIMS:
        raise ValueError(
            f"EMBEDDING_MODEL {config.EMBEDDING_MODEL} (Titan v2) only supports "
            f"dimensions {_VALID_TITAN_DIMS}, got {config.VECTOR_DIMENSION}."
        )
    body = {
        "inputText": text[:8000],  # Titan token limits; truncate safely
        "dimensions": config.VECTOR_DIMENSION,
        "normalize": True,
    }
    resp = _bedrock.invoke_model(
        modelId=config.EMBEDDING_MODEL,
        body=json.dumps(body),
    )
    payload = json.loads(resp["body"].read())
    return payload["embedding"]