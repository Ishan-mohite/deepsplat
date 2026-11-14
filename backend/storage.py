"""Object storage: local folder (development) or Google Cloud Storage (deployment)."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import settings


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def put_file(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> int: ...
    def exists(self, key: str) -> bool: ...
    def local_path(self, key: str) -> Path: ...          # readable local copy
    def url(self, key: str) -> str: ...                  # GET URL for clients
    def upload_url(self, key: str, content_type: str) -> tuple[str, datetime]: ...   # PUT URL


class LocalStorage:
    def __init__(self, root: Path, base_url: str):
        self.root = root
        self.base_url = base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError(f"invalid key {key!r}")
        return p

    def put_bytes(self, key, data, content_type=None):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, key, fileobj, content_type=None):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            shutil.copyfileobj(fileobj, f, length=1024 * 1024)
        return p.stat().st_size

    def exists(self, key):
        return self._path(key).is_file()

    def local_path(self, key):
        return self._path(key)

    def url(self, key):
        return f"{self.base_url}/files/{key}"

    def upload_url(self, key, content_type):
        # served by PUT /v1/uploads/{key} (local development stand-in for a GCS signed URL)
        return f"{self.base_url}/v1/uploads/{key}", datetime.now(UTC) + timedelta(hours=1)


class GCSStorage:
    """GCS with V4 signed URLs. On Cloud Run the service account needs
    roles/iam.serviceAccountTokenCreator on itself to sign URLs."""

    def __init__(self, bucket: str, cache_dir: Path):
        import google.auth
        from google.auth.transport import requests as ga_requests
        from google.cloud import storage

        self._creds, _ = google.auth.default()
        self._auth_request = ga_requests.Request()
        self.bucket = storage.Client().bucket(bucket)
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)

    def _sign(self, key: str, method: str, expires: timedelta, content_type: str | None = None) -> str:
        if not self._creds.valid:
            self._creds.refresh(self._auth_request)
        kw = {}
        if hasattr(self._creds, "service_account_email") and getattr(self._creds, "token", None):
            kw = {"service_account_email": self._creds.service_account_email, "access_token": self._creds.token}
        return self.bucket.blob(key).generate_signed_url(version="v4", expiration=expires, method=method,
                                                          content_type=content_type, **kw)

    def put_bytes(self, key, data, content_type=None):
        self.bucket.blob(key).upload_from_string(data, content_type=content_type)

    def put_file(self, key, fileobj, content_type=None):
        blob = self.bucket.blob(key)
        blob.upload_from_file(fileobj, content_type=content_type)
        blob.reload()
        return blob.size

    def exists(self, key):
        return self.bucket.blob(key).exists()

    def local_path(self, key):
        p = self.cache / key
        if not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            self.bucket.blob(key).download_to_filename(p)
        return p

    def url(self, key):
        return self._sign(key, "GET", timedelta(days=7))

    def upload_url(self, key, content_type):
        expires = timedelta(hours=1)
        return self._sign(key, "PUT", expires, content_type), datetime.now(UTC) + expires


def _make() -> Storage:
    if settings.storage == "gcs":
        return GCSStorage(settings.gcs_bucket, settings.data_dir / "cache")
    return LocalStorage(settings.data_dir / "blobs", settings.public_base_url)


storage: Storage = _make()
