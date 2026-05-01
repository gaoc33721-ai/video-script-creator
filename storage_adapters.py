import io
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root_dir / key).resolve()
        try:
            path.relative_to(self.root_dir.resolve())
        except ValueError as exc:
            raise StorageError(f"Refusing path outside storage root: {key}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        self._path(key).write_bytes(data)
        return key


class S3Storage:
    def __init__(self, bucket: str, prefix: str = "", region_name: str | None = None):
        try:
            import boto3
            from botocore.exceptions import ClientError
        except ModuleNotFoundError as exc:
            raise StorageError("boto3 is required for S3 storage. Run `pip install -r requirements.txt`.") from exc

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client_error = ClientError
        self.client = boto3.client("s3", region_name=region_name)

    def _key(self, key: str) -> str:
        key = key.lstrip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except self.client_error as exc:
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                return False
            raise

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        return response["Body"].read()

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(key), "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        self.client.put_object(**kwargs)
        return f"s3://{self.bucket}/{self._key(key)}"


class RuntimeStorage:
    def __init__(self):
        backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        if backend == "s3":
            bucket = os.getenv("S3_BUCKET") or os.getenv("APP_S3_BUCKET")
            if not bucket:
                raise StorageError("S3_BUCKET or APP_S3_BUCKET is required when STORAGE_BACKEND=s3.")
            self.backend = S3Storage(bucket, os.getenv("S3_PREFIX", ""), region_name=region)
        else:
            self.backend = LocalStorage(os.getenv("APP_DATA_DIR", "."))

    def exists(self, key: str) -> bool:
        return self.backend.exists(key)

    def read_json(self, key: str, default_value: Any) -> Any:
        if not self.exists(key):
            return default_value
        try:
            return json.loads(self.backend.read_bytes(key).decode("utf-8"))
        except Exception:
            return default_value

    def write_json(self, key: str, payload: Any) -> bool:
        try:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.backend.write_bytes(key, data, content_type="application/json; charset=utf-8")
            return True
        except Exception:
            return False

    def read_dataframe(self, key: str) -> pd.DataFrame:
        # Prefer safe Parquet format; fall back to legacy pickle for migration.
        parquet_key = key.replace(".pkl", ".parquet") if key.endswith(".pkl") else key + ".parquet"
        if self.exists(parquet_key):
            try:
                return pd.read_parquet(io.BytesIO(self.backend.read_bytes(parquet_key)))
            except Exception:
                pass
        if not self.exists(key):
            return pd.DataFrame()
        try:
            df = pd.read_pickle(io.BytesIO(self.backend.read_bytes(key)))
            # Auto-migrate: write parquet copy so pickle is no longer needed.
            try:
                buf = io.BytesIO()
                df.to_parquet(buf, index=False)
                self.backend.write_bytes(parquet_key, buf.getvalue(), content_type="application/octet-stream")
            except Exception:
                pass
            return df
        except Exception:
            return pd.DataFrame()

    def write_dataframe(self, key: str, df: pd.DataFrame) -> bool:
        parquet_key = key.replace(".pkl", ".parquet") if key.endswith(".pkl") else key + ".parquet"
        try:
            buffer = io.BytesIO()
            df.to_parquet(buffer, index=False)
            self.backend.write_bytes(parquet_key, buffer.getvalue(), content_type="application/octet-stream")
            return True
        except Exception:
            # Fallback to pickle if pyarrow is not installed.
            try:
                buffer = io.BytesIO()
                df.to_pickle(buffer)
                self.backend.write_bytes(key, buffer.getvalue(), content_type="application/octet-stream")
                return True
            except Exception:
                return False

    def write_file_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        return self.backend.write_bytes(key, data, content_type=content_type)
