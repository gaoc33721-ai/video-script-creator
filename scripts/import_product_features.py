import argparse
import os
from pathlib import Path

import pandas as pd

from product_feature_store import filter_product_features

try:
    import psycopg
except ModuleNotFoundError:  # Allows dry-run validation before DB deps are installed.
    psycopg = None


def load_features(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    return filter_product_features(df)


def insert_features(conninfo: str, file_name: str, s3_key: str | None, created_by: str | None, df: pd.DataFrame) -> str:
    if psycopg is None:
        raise RuntimeError("psycopg is not installed. Run `pip install -r requirements.txt` first.")

    with psycopg.connect(conninfo) as conn:
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
                    s3_key,
                    len(df),
                    int(df["model"].nunique()),
                    int(df["Category"].nunique()),
                    created_by,
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
                for _, r in df.iterrows()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Import product feature Excel into PostgreSQL.")
    parser.add_argument("excel_path", type=Path)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--s3-key", default=os.getenv("PRODUCT_FEATURE_S3_KEY"))
    parser.add_argument("--created-by", default=os.getenv("IMPORT_CREATED_BY"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    df = load_features(args.excel_path)
    print(
        f"Loaded {len(df)} rows, {df['model'].nunique()} models, "
        f"{df['Category'].nunique()} categories from {args.excel_path.name}."
    )

    if args.dry_run:
        print("Dry run only; no database writes performed.")
        return

    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required unless --dry-run is set.")

    version_id = insert_features(args.database_url, args.excel_path.name, args.s3_key, args.created_by, df)
    print(f"Imported active product feature version: {version_id}")


if __name__ == "__main__":
    main()
