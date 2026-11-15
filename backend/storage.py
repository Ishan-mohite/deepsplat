"""Object storage: local folder (development) or Google Cloud Storage (deployment)."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import settings


def _delete_key(key: str) -> str:
    """Deletion always targets one relative object or directory, never the store root."""
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in key.split("/")):
        raise ValueError(f"invalid deletion key {key!r}")
    return key


def _delete_path(root: Path, key: str) -> Path:
    path = root.resolve()
    for part in _delete_key(key).split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError(f"refusing to delete through a symlink: {key!r}")
    return path


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def put_file(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> int: ...
    def exists(self, key: str) -> bool: ...
    def local_path(self, key: str) -> Path: ...          # readable local copy
    def url(self, key: str) -> str: ...                  # GET URL for clients
    def upload_url(self, key: str, content_type: str) -> tuple[str, datetime]: ...   # PUT URL
    def list_keys(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> bool: ...  # True if deleted, False if already missing
    def delete_prefix(self, prefix: str) -> int: ...  # directory boundary; count deleted objects


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

    def list_keys(self, prefix):
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def upload_url(self, key, content_type):
        # served by PUT /v1/uploads/{key} (local development stand-in for a GCS signed URL)
        return f"{self.base_url}/v1/uploads/{key}", datetime.now(UTC) + timedelta(hours=1)

    def delete(self, key):
        path = _delete_path(self.root, key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def delete_prefix(self, prefix):
        base = _delete_path(self.root, prefix.removesuffix("/"))
        if not base.exists():
            return 0
        if not base.is_dir():
            return 0
        count = sum(p.is_file() for p in base.rglob("*"))
        shutil.rmtree(base)
        return count


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

    def list_keys(self, prefix):
        return sorted(b.name for b in self.bucket.client.list_blobs(self.bucket, prefix=prefix))

    def upload_url(self, key, content_type):
        expires = timedelta(hours=1)
        return self._sign(key, "PUT", expires, content_type), datetime.now(UTC) + expires

    def delete(self, key):
        from google.api_core.exceptions import NotFound

        key = _delete_key(key)
        cache_path = _delete_path(self.cache, key)
        try:
            self.bucket.blob(key).delete()
            deleted = True
        except NotFound:
            deleted = False
        cache_path.unlink(missing_ok=True)
        return deleted

    def delete_prefix(self, prefix):
        key = _delete_key(prefix.removesuffix("/"))
        # Always include the slash: captures/abc must never match captures/abcd.
        cache_path = _delete_path(self.cache, key)
        count = sum(self.delete(k) for k in self.list_keys(f"{key}/"))
        # Remove stale cached files too, including objects already absent from GCS.
        if cache_path.exists():
            shutil.rmtree(cache_path)
        return count


class StorageIndexSync:
    """Keeps VPS index folders in object storage under vps/<map_id>/ (survives restarts, shared
    between Cloud Run instances)."""

    def __init__(self, store: "Storage"):
        self.store = store

    def push(self, map_id: str, local_dir) -> None:
        local_dir = Path(local_dir)
        for f in local_dir.rglob("*"):
            if f.is_file():
                with open(f, "rb") as fh:
                    self.store.put_file(f"vps/{map_id}/{f.relative_to(local_dir).as_posix()}", fh)

    def pull(self, map_id: str, local_dir) -> bool:
        keys = self.store.list_keys(f"vps/{map_id}/")
        if not keys:
            return False
        local_dir = Path(local_dir)
        for k in keys:
            dst = local_dir / k.removeprefix(f"vps/{map_id}/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.store.local_path(k), dst)
        return True


def _make() -> Storage:
    if settings.storage == "gcs":
        return GCSStorage(settings.gcs_bucket, settings.data_dir / "cache")
    return LocalStorage(settings.data_dir / "blobs", settings.public_base_url)


storage: Storage = _make()
