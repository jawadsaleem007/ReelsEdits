"""Object storage.

One interface, two backends. Local disk runs the app with zero infrastructure;
S3 is the production path. The interface is deliberately the *S3* shape — keys,
presigned URLs, content-addressed paths — so the local backend cannot quietly
grow habits (directory walks, path arithmetic, mutation in place) that would not
survive the migration.

Media bytes never pass through the API tier in production: clients PUT directly
to presigned URLs. The local backend keeps that shape by issuing URLs that point
at a dedicated upload endpoint rather than letting callers write files directly.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO


def content_key(org_id: str, asset_id: str, filename: str) -> str:
    """Org-scoped key. The prefix is what bucket policies deny across."""
    safe = Path(filename).name.replace("/", "_")[:120] or "file"
    return f"{org_id}/{asset_id}/{safe}"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, source: BinaryIO | Path) -> int:
        """Store an object. Returns bytes written."""

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """A real filesystem path for the object.

        ffmpeg needs a file. The S3 backend downloads to a cache directory; the
        local backend returns the file directly.
        """

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def url_for(self, key: str) -> str:
        """A URL a browser can fetch the object from."""

    @abstractmethod
    def size(self, key: str) -> int: ...


class LocalStorage(Storage):
    """Filesystem-backed. Development and single-node deployments."""

    def __init__(self, root: Path, public_prefix: str = "/v1/files") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_prefix = public_prefix.rstrip("/")

    def _p(self, key: str) -> Path:
        # Reject traversal explicitly rather than relying on resolve() ordering.
        if ".." in Path(key).parts or Path(key).is_absolute():
            raise ValueError(f"unsafe storage key: {key!r}")
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"storage key escapes root: {key!r}")
        return p

    def put(self, key: str, source: BinaryIO | Path) -> int:
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, Path):
            shutil.copyfile(source, dest)
        else:
            with dest.open("wb") as f:
                shutil.copyfileobj(source, f)
        return dest.stat().st_size

    def local_path(self, key: str) -> Path:
        p = self._p(key)
        if not p.exists():
            raise FileNotFoundError(key)
        return p

    def exists(self, key: str) -> bool:
        try:
            return self._p(key).exists()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        # An unsafe key cannot name a stored object, so there is nothing to
        # delete and nothing to report.
        with contextlib.suppress(ValueError):
            self._p(key).unlink(missing_ok=True)

    def url_for(self, key: str) -> str:
        return f"{self.public_prefix}/{key}"

    def size(self, key: str) -> int:
        return self._p(key).stat().st_size


class S3Storage(Storage):
    """Production backend. Keys and lifecycle policies per docs/03 §3.5."""

    def __init__(self, bucket: str, region: str = "us-east-1",
                 endpoint_url: str | None = None, cache_dir: Path | None = None) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)
        self.cache_dir = Path(
            cache_dir or Path(tempfile.gettempdir()) / "reelsedits-cache"
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, source: BinaryIO | Path) -> int:
        if isinstance(source, Path):
            self.client.upload_file(str(source), self.bucket, key)
            return source.stat().st_size
        self.client.upload_fileobj(source, self.bucket, key)
        return self.size(key)

    def local_path(self, key: str) -> Path:
        # Content-addressed cache path: the same key never downloads twice.
        cached = self.cache_dir / hashlib.sha256(key.encode()).hexdigest()[:24] / Path(key).name
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, key, str(cached))
        return cached

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def url_for(self, key: str, ttl: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl
        )

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])


_storage: Storage | None = None


def init_storage(backend: str, **kwargs) -> Storage:
    global _storage
    _storage = LocalStorage(**kwargs) if backend == "local" else S3Storage(**kwargs)
    return _storage


def get_storage() -> Storage:
    if _storage is None:
        raise RuntimeError("storage not initialised; call init_storage() first")
    return _storage
