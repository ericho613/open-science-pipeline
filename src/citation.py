"""Generate APA citations via Amazon Bedrock (Claude Haiku)."""
import json
import boto3
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .config import config
from botocore.exceptions import ClientError

_bedrock = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


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
def generate_apa_citation(title: str, url: str, sample_text: str = "") -> str:
    prompt = (
        "Create an APA 7th edition citation for the following academic article. "
        "Return ONLY the citation text, no preamble.\n\n"
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Excerpt: {sample_text[:1500]}\n"
    )
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = _bedrock.invoke_model(
        modelId=config.CITATION_MODEL,
        body=json.dumps(body),
    )
    payload = json.loads(resp["body"].read())
    try:
        return payload["content"][0]["text"].strip()
    except (KeyError, IndexError):
        return f"{title}. Retrieved from {url}"