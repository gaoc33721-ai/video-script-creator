import os
import re
from typing import Any

import pandas as pd
import requests

from product_feature_store import FEATURE_COLUMNS


class ProductCatalogApiError(RuntimeError):
    pass


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _field(row: dict, *keys: str) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _split_selling_point_names(row: dict) -> list[str]:
    names: list[str] = []
    details = row.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            name = _field(detail, "pointNameEn", "pointName", "name")
            if name and name not in names:
                names.append(name)
    if not names:
        for item in re.split(r"[,;\uFF1B\uFF0C]\s*", _field(row, "pointName")):
            item = _clean(item)
            if item and item not in names:
                names.append(item)
    return names


def _preferred_copy_detail(copy_row: dict) -> dict:
    details = copy_row.get("details")
    if not isinstance(details, list):
        return {}
    fallback = next((item for item in details if isinstance(item, dict)), {})
    for detail in details:
        if not isinstance(detail, dict):
            continue
        language = _field(detail, "languageEnglish", "languageChinese").lower()
        if language in {"global", "english"}:
            return detail
    return fallback if isinstance(fallback, dict) else {}


def _selling_point_copy_maps(copy_rows: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for row in copy_rows:
        if not isinstance(row, dict):
            continue
        row_id = _field(row, "id", "pointId")
        point_name = _field(row, "pointName", "pointNameEn")
        if row_id:
            by_id[row_id] = row
        if point_name:
            by_name[point_name.lower()] = row
    return by_id, by_name


def _copy_row_for_point(point: dict, name: str, by_id: dict[str, dict], by_name: dict[str, dict]) -> dict:
    point_id = _field(point, "pointId", "id")
    if point_id and point_id in by_id:
        return by_id[point_id]
    return by_name.get(_clean(name).lower(), {})


def _parameter_lines(parameters: Any) -> list[str]:
    lines: list[str] = []
    for raw in str(parameters or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split("\t") if part.strip()]
        lines.append(": ".join(parts) if len(parts) > 1 else parts[0])
    return lines


def _parameter_summary(parameters: Any, max_chars: int = 1200) -> str:
    text = "; ".join(_parameter_lines(parameters))
    return text[:max_chars].rstrip()


def _model_key(row: dict) -> str:
    return "|".join(
        [
            _field(row, "model").lower(),
            _field(row, "country", "countryEnglish", "countryChinese").lower(),
            _field(row, "brandId", "brandIdEnglish", "brandIdChinese").lower(),
            _field(row, "productLineId", "productLineIdEnglish", "productLineIdChinese").lower(),
        ]
    )


def _loose_model_key(row: dict) -> str:
    return _field(row, "model").lower()


def _best_model_detail(row: dict, model_details: dict[str, dict], loose_details: dict[str, dict]) -> dict:
    return model_details.get(_model_key(row)) or loose_details.get(_loose_model_key(row)) or {}


def _feature_description(
    selling_row: dict,
    point_name: str,
    model_row: dict | None = None,
    copy_row: dict | None = None,
) -> str:
    copy_detail = _preferred_copy_detail(copy_row or {})
    copy_text = _field(copy_detail, "longCopy", "shortCopy", "other")
    parts = [copy_text or point_name]
    for key in ("productPositioning", "introduction", "other"):
        value = _field(selling_row, key)
        if value and value not in parts:
            parts.append(value)
    parameter_text = _parameter_summary((model_row or {}).get("parameters"))
    if parameter_text:
        parts.append(f"Model specifications: {parameter_text}")
    return " | ".join(parts)


def product_catalog_rows_to_features(
    model_rows: list[dict],
    selling_point_rows: list[dict],
    selling_point_copy_rows: list[dict] | None = None,
    include_parameter_fallbacks: bool = True,
) -> pd.DataFrame:
    model_details = {_model_key(item): item for item in model_rows if _field(item, "model")}
    loose_details = {_loose_model_key(item): item for item in model_rows if _field(item, "model")}
    copy_by_id, copy_by_name = _selling_point_copy_maps(selling_point_copy_rows or [])
    feature_rows: list[dict[str, Any]] = []
    models_with_selling_points: set[str] = set()

    for item in selling_point_rows:
        model = _field(item, "model")
        if not model:
            continue
        model_detail = _best_model_detail(item, model_details, loose_details)
        models_with_selling_points.add(model.lower())
        point_details = item.get("details") if isinstance(item.get("details"), list) else []
        if point_details:
            point_items = [
                (detail, _field(detail, "pointNameEn", "pointName", "name"))
                for detail in point_details
                if isinstance(detail, dict)
            ]
        else:
            point_items = [({}, point_name) for point_name in _split_selling_point_names(item)]

        for point_detail, point_name in point_items:
            if not point_name:
                continue
            copy_row = _copy_row_for_point(point_detail, point_name, copy_by_id, copy_by_name)
            copy_detail = _preferred_copy_detail(copy_row)
            tagline = _field(copy_detail, "slogan", "shortCopy") or point_name
            feature_rows.append(
                {
                    "Region": _field(item, "countryEnglish", "countryChinese") or "Global",
                    "Brand": _field(item, "brandIdEnglish", "brandIdChinese") or "Hisense",
                    "Category": _field(item, "productLineIdEnglish", "productLineIdChinese"),
                    "model": model,
                    "language": "English",
                    "Feature Name": point_name,
                    "Tagline": tagline,
                    "Feature Description": _feature_description(item, point_name, model_detail, copy_row),
                }
            )

    if include_parameter_fallbacks:
        for item in model_rows:
            model = _field(item, "model")
            parameter_text = _parameter_summary(item.get("parameters"))
            if not model or not parameter_text or model.lower() in models_with_selling_points:
                continue
            feature_rows.append(
                {
                    "Region": _field(item, "countryEnglish", "countryChinese") or "Global",
                    "Brand": _field(item, "brandIdEnglish", "brandIdChinese") or "Hisense",
                    "Category": _field(item, "productLineIdEnglish", "productLineIdChinese"),
                    "model": model,
                    "language": "English",
                    "Feature Name": "Product Specifications",
                    "Tagline": "Key product specifications",
                    "Feature Description": parameter_text,
                }
            )

    if not feature_rows:
        return pd.DataFrame(columns=FEATURE_COLUMNS)
    df = pd.DataFrame(feature_rows)
    df = df[FEATURE_COLUMNS].fillna("")
    df = df[
        df["model"].astype(str).str.strip().ne("")
        & df["Category"].astype(str).str.strip().ne("")
        & df["Feature Description"].astype(str).str.strip().ne("")
    ]
    return df.drop_duplicates(subset=["Region", "Brand", "Category", "model", "Feature Name"]).reset_index(drop=True)


class ProductCatalogClient:
    def __init__(
        self,
        base_url: str,
        model_details_path: str = "/product/open/material/pageSysProductModelWithDetails",
        selling_points_path: str = "/product/open/material/pageProductSysSellingPointsWithDetails",
        selling_point_copies_path: str = "/product/open/material/pageSysSellingPointsWithDetails",
        timeout: int = 25,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_details_path = model_details_path
        self.selling_points_path = selling_points_path
        self.selling_point_copies_path = selling_point_copies_path
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "ProductCatalogClient":
        return cls(
            base_url=os.getenv("MSC_PRODUCT_CATALOG_BASE_URL", "https://msc.hisense.com/api/msc-product"),
            model_details_path=os.getenv(
                "MSC_PRODUCT_MODEL_DETAILS_PATH",
                "/product/open/material/pageSysProductModelWithDetails",
            ),
            selling_points_path=os.getenv(
                "MSC_PRODUCT_SELLING_POINTS_PATH",
                "/product/open/material/pageProductSysSellingPointsWithDetails",
            ),
            selling_point_copies_path=os.getenv(
                "MSC_PRODUCT_SELLING_POINT_COPIES_PATH",
                "/product/open/material/pageSysSellingPointsWithDetails",
            ),
            timeout=max(5, int(os.getenv("MSC_PRODUCT_CATALOG_TIMEOUT", "25"))),
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{str(path or '').lstrip('/')}"

    def _post_page(self, path: str, payload: dict[str, Any]) -> dict:
        response = requests.post(
            self._url(path),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if str(body.get("code")) != "0":
            raise ProductCatalogApiError(body.get("msg") or body.get("message") or "Product catalog API error.")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ProductCatalogApiError("Product catalog API returned invalid data.")
        return data

    def fetch_pages(
        self,
        path: str,
        filters: dict[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(page_size or 100), 500))
        max_pages = max(0, int(max_pages or 0))
        page_num = 1
        rows: list[dict] = []
        total = 0

        while True:
            payload = dict(filters or {})
            payload["pageNum"] = page_num
            payload["pageSize"] = page_size
            data = self._post_page(path, payload)
            page_rows = data.get("rows") or []
            if not isinstance(page_rows, list):
                raise ProductCatalogApiError("Product catalog API returned invalid rows.")
            rows.extend([item for item in page_rows if isinstance(item, dict)])
            total = int(data.get("total") or total or len(rows))
            if not page_rows:
                break
            if total and page_num * page_size >= total:
                break
            if max_pages and page_num >= max_pages:
                break
            page_num += 1

        return {"rows": rows, "total": total, "page_count": page_num}

    def fetch_model_details(self, filters: dict[str, Any] | None = None, page_size: int = 100, max_pages: int = 0) -> dict:
        return self.fetch_pages(self.model_details_path, filters=filters, page_size=page_size, max_pages=max_pages)

    def fetch_selling_points(self, filters: dict[str, Any] | None = None, page_size: int = 100, max_pages: int = 0) -> dict:
        return self.fetch_pages(self.selling_points_path, filters=filters, page_size=page_size, max_pages=max_pages)

    def fetch_selling_point_copies(
        self,
        filters: dict[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int = 0,
    ) -> dict:
        return self.fetch_pages(self.selling_point_copies_path, filters=filters, page_size=page_size, max_pages=max_pages)