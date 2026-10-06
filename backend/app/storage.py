import os
import shutil
from datetime import UTC, datetime, timedelta
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
    # filename is client-supplied (UploadFile.filename) — strip any directory
    # components before it touches a path, or "../../../etc/x" would let an
    # uploader write files outside LOCAL_STORAGE_ROOT. Handle both slash
    # styles since a client can send either regardless of the server's OS.
    safe_filename = os.path.basename(filename.replace("\\", "/")) or "file"
    key = f"companies/{company_id}/{uuid4()}-{safe_filename}"
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


def upload_backup_bytes(key: str, data: bytes, content_type: str = "application/zip") -> str:
    """Platform-level upload with an arbitrary key -- not under companies/{id}/..."""
    ensure_bucket()
    if use_local_storage():
        path = LOCAL_STORAGE_ROOT / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)
    s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
    return key


def upload_backup_file(key: str, path: str, content_type: str = "application/zip") -> str:
    """Same as upload_backup_bytes() but streams from a file on disk (multipart for big files)."""
    ensure_bucket()
    if use_local_storage():
        dest = LOCAL_STORAGE_ROOT / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return str(dest)
    s3_client().upload_file(path, settings.s3_bucket, key, ExtraArgs={"ContentType": content_type})
    return key


def delete_old_backups(prefix: str, keep_days: int) -> list[str]:
    """Deletes objects under `prefix` older than keep_days. Returns what was deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    deleted: list[str] = []
    if use_local_storage():
        root = LOCAL_STORAGE_ROOT / prefix
        if root.exists():
            for f in root.iterdir():
                if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime, tz=UTC) < cutoff:
                    f.unlink()
                    deleted.append(str(f))
        return deleted
    client = s3_client()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                client.delete_object(Bucket=settings.s3_bucket, Key=obj["Key"])
                deleted.append(obj["Key"])
    return deleted
