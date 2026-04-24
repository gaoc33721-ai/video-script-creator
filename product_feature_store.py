import os
import re
from datetime import datetime

import pandas as pd


FEATURE_COLUMNS = [
    "Region",
    "Brand",
    "Category",
    "model",
    "language",
    "Feature Name",
    "Tagline",
    "Feature Description",
]


def filter_product_features(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in FEATURE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"产品卖点库缺少必要字段：{', '.join(missing)}")

    filtered = df[FEATURE_COLUMNS].copy()
    mask = filtered["language"].astype(str).str.contains("英语|全球通用版", na=False)
    filtered = filtered[mask].dropna(subset=["Feature Description", "model", "Category"])
    return filtered.fillna("")


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return slug or "product_features"


def build_upload_key(file_name: str) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"uploads/product-features/{timestamp}_{_safe_slug(file_name)}"


class ProductFeatureStore:
    def __init__(self, storage):
        self.storage = storage
        self.database_url = os.getenv("DATABASE_URL", "").strip()

    @property
    def uses_database(self) -> bool:
        return bool(self.database_url)

    def load(self) -> pd.DataFrame:
        if self.uses_database:
            db_df = self._load_from_postgres()
            if not db_df.empty:
                return db_df
        return self.storage.read_dataframe("cached_product_features.pkl")

    def save(self, file_name: str, original_bytes: bytes, df_filtered: pd.DataFrame) -> dict:
        upload_key = build_upload_key(file_name)
        stored_uri = self.storage.write_file_bytes(
            upload_key,
            original_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        if self.uses_database:
            version_id = self._save_to_postgres(file_name, stored_uri, df_filtered)
        else:
            version_id = None
            self.storage.write_dataframe("cached_product_features.pkl", df_filtered)

        meta = {
            "file_name": file_name,
            "storage_uri": stored_uri,
            "version_id": version_id,
            "row_count": int(len(df_filtered)),
            "model_count": int(df_filtered["model"].nunique()) if "model" in df_filtered.columns else 0,
            "category_count": int(df_filtered["Category"].nunique()) if "Category" in df_filtered.columns else 0,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "storage_backend": os.getenv("STORAGE_BACKEND", "local"),
            "database_enabled": self.uses_database,
        }
        self.storage.write_json("cache_meta.json", meta)
        return meta

    def _load_from_postgres(self) -> pd.DataFrame:
        try:
            import psycopg
        except ModuleNotFoundError:
            return pd.DataFrame()

        query = """
            select
              pf.region as "Region",
              pf.brand as "Brand",
              pf.category as "Category",
              pf.model as "model",
              pf.language as "language",
              pf.feature_name as "Feature Name",
              pf.tagline as "Tagline",
              pf.feature_description as "Feature Description"
            from product_features pf
            join product_feature_versions v on v.id = pf.version_id
            where v.is_active = true
            order by pf.category, pf.model, pf.feature_name
        """
        try:
            with psycopg.connect(self.database_url) as conn:
                return pd.read_sql(query, conn)
        except Exception:
            return pd.DataFrame()

    def _save_to_postgres(self, file_name: str, stored_uri: str, df_filtered: pd.DataFrame) -> str:
        try:
            import psycopg
        except ModuleNotFoundError as exc:
            raise RuntimeError("psycopg is required when DATABASE_URL is set.") from exc

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("update product_feature_versions set is_active = false where is_active = true")
                cur.execute(
                    """
                    insert into product_feature_versions
                      (file_name, s3_key, row_count, model_count, category_count, created_by, is_active)
                    values (%s, %s, %s, %s, %s, %s, true)
                    returning id
                    """,
                    (
                        file_name,
                        stored_uri,
                        len(df_filtered),
                        int(df_filtered["model"].nunique()),
                        int(df_filtered["Category"].nunique()),
                        os.getenv("APP_IMPORT_USER", "streamlit-upload"),
                    ),
                )
                version_id = str(cur.fetchone()[0])

                rows = [
                    (
                        version_id,
                        r["Region"],
                        r["Brand"],
                        r["Category"],
                        r["model"],
                        r["language"],
                        r["Feature Name"],
                        r["Tagline"],
                        r["Feature Description"],
                    )
                    for _, r in df_filtered.iterrows()
                ]
                cur.executemany(
                    """
                    insert into product_features
                      (version_id, region, brand, category, model, language, feature_name, tagline, feature_description)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            conn.commit()
        return version_id
