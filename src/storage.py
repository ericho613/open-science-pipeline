"""Amazon S3 uploads for figure images."""
import os
import boto3
from .config import config

_s3 = boto3.client("s3", region_name=config.AWS_REGION)


def upload_figure(local_path: str, key: str) -> str:
    """Upload a PNG and return its public/object URL."""
    _s3.upload_file(
        local_path,
        config.S3_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": "image/png"},
    )
    if config.S3_PUBLIC_BASE_URL:
        return f"{config.S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"
    return (
        f"https://{config.S3_BUCKET_NAME}.s3.{config.AWS_REGION}"
        f".amazonaws.com/{key}"
    )