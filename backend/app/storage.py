from uuid import uuid4

import boto3
from botocore.client import Config
from pathlib import Path

from app.config import get_settings


settings = get_settings()
LOCAL_STORAGE_ROOT = Path("local-storage")


def use_local_storage() -> bool:
    return not settings.s3_access_key_id or not settings.s3_secret_access_key


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.aws_region,
        config=Config(signature_version="s3v4"),
    )


def ensure_bucket() -> None:
    if use_local_storage():
        LOCAL_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        return
    client = s3_client()
    buckets = client.list_buckets().get("Buckets", [])
    if not any(bucket["Name"] == settings.s3_bucket for bucket in buckets):
        client.create_bucket(Bucket=settings.s3_bucket)


def upload_fileobj(company_id: str, filename: str, content_type: str, fileobj) -> str:
    ensure_bucket()
    key = f"companies/{company_id}/{uuid4()}-{filename}"
    if use_local_storage():
        path = LOCAL_STORAGE_ROOT / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as output:
            output.write(fileobj.read())
        return str(path)
    s3_client().upload_fileobj(
        fileobj,
        settings.s3_bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return key
