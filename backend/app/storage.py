"""Blob storage behind a small interface.

LocalStorage is used for development. For deployment, implement the same interface on top of
an S3-compatible client (works for Cloudflare R2 and Google Cloud Storage's S3 interop API)
and return signed URLs from `url()`.
"""

import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import settings


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def put_file(self, key: str, fileobj: BinaryIO) -> int: ...
    def local_path(self, key: str) -> Path: ...
    def url(self, key: str) -> str: ...


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

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, key: str, fileobj: BinaryIO) -> int:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            shutil.copyfileobj(fileobj, f, length=1024 * 1024)
        return p.stat().st_size

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def url(self, key: str) -> str:
        return f"{self.base_url}/files/{key}"


storage: Storage = LocalStorage(settings.data_dir / "blobs", settings.public_base_url)
