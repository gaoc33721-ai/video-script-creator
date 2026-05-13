from __future__ import annotations

import datetime as dt
import math
import re
import time
import urllib.parse
from typing import Any

import requests


RAINFOREST_ENDPOINT = "https://api.rainforestapi.com/request"


class RainforestApiError(RuntimeError):
    pass


def clean_asin(value: str) -> str:
    asin = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return asin[:20]


def amazon_domain_for_market(target_market: str = "", explicit_domain: str = "", default_domain: str = "amazon.com") -> str:
    if explicit_domain:
        return explicit_domain.strip()
    market = str(target_market or "").lower()
    if "de" in market or "germany" in market or "德国" in market:
        return "amazon.de"
    if "fr" in market or "france" in market or "法国" in market:
        return "amazon.fr"
    if "uk" in market or "gb" in market or "英国" in market or "欧洲" in market:
        return "amazon.co.uk"
    return default_domain or "amazon.com"


def build_search_queries(category: str, brands: list[str] | None, keywords: list[str] | None, limit: int) -> list[str]:
    category = str(category or "").strip()
    clean_brands = _unique_strings(brands or [])
    clean_keywords = _unique_strings(keywords or [])
    seed_terms = clean_keywords or ([category] if category else [])
    queries: list[str] = []

    for keyword in seed_terms:
        if clean_brands:
            for brand in clean_brands:
                queries.append(f"{brand} {keyword}".strip())
        queries.append(keyword)

    if category and clean_brands:
        for brand in clean_brands:
            queries.append(f"{brand} {category}".strip())
    elif clean_brands:
        queries.extend(clean_brands)

    return _unique_strings(queries)[: max(1, int(limit or 1))]


def discover_asins(
    api_key: str,
    *,
    category: str = "",
    brands: list[str] | None = None,
    keywords: list[str] | None = None,
    target_market: str = "",
    amazon_domain: str = "",
    default_domain: str = "amazon.com",
    max_results: int = 5,
    request_limit: int = 6,
    sort_by: str = "featured",
    timeout: int = 20,
) -> dict[str, Any]:
    domain = amazon_domain_for_market(target_market, amazon_domain, default_domain)
    queries = build_search_queries(category, brands, keywords, request_limit)
    if not queries:
        raise RainforestApiError("请至少提供品类、品牌或关键词用于发现 ASIN。")

    seen: set[str] = set()
    asins: list[dict[str, Any]] = []
    query_results: list[dict[str, Any]] = []

    for query in queries:
        data = rainforest_request(
            api_key,
            {
                "type": "search",
                "amazon_domain": domain,
                "search_term": query,
                "sort_by": sort_by or "featured",
            },
            timeout=timeout,
        )
        results = data.get("search_results") or []
        query_asins: list[str] = []
        for index, item in enumerate(results, start=1):
            asin = clean_asin((item or {}).get("asin", ""))
            if not asin:
                continue
            query_asins.append(asin)
            if asin in seen:
                continue
            seen.add(asin)
            asins.append(
                {
                    "asin": asin,
                    "title": str((item or {}).get("title", "") or ""),
                    "brand": str((item or {}).get("brand", "") or ""),
                    "position": index,
                    "source_query": query,
                    "amazon_domain": domain,
                    "product_url": _product_url(domain, asin),
                }
            )
            if len(asins) >= max(1, int(max_results or 1)):
                break
        query_results.append({"query": query, "result_count": len(results), "asins": query_asins[: int(max_results or 5)]})
        if len(asins) >= max(1, int(max_results or 1)):
            break
        time.sleep(0.15)

    return {"amazon_domain": domain, "queries": query_results, "asins": asins}


def fetch_product(
    api_key: str,
    asin: str,
    *,
    amazon_domain: str = "amazon.com",
    timeout: int = 30,
) -> dict[str, Any]:
    clean = clean_asin(asin)
    if not clean:
        raise RainforestApiError("ASIN 为空或格式无效。")
    return rainforest_request(
        api_key,
        {
            "type": "product",
            "amazon_domain": amazon_domain or "amazon.com",
            "asin": clean,
            "include_image_block_videos": "true",
        },
        timeout=timeout,
    )


def rainforest_request(api_key: str, params: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
    if not api_key:
        raise RainforestApiError("RAINFOREST_API_KEY 未配置。")
    request_params = {"api_key": api_key, **params}
    try:
        response = requests.get(RAINFOREST_ENDPOINT, params=request_params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise RainforestApiError(f"Rainforest 网络请求失败：{exc}") from exc
    except ValueError as exc:
        raise RainforestApiError("Rainforest 返回不是合法 JSON。") from exc

    request_info = data.get("request_info") or {}
    if request_info.get("success") is False:
        message = request_info.get("message") or request_info.get("error") or "Rainforest API 请求失败。"
        raise RainforestApiError(str(message))
    return data


def normalize_product_response(
    data: dict[str, Any],
    *,
    asin: str,
    amazon_domain: str,
    category: str = "",
    source_query: str = "",
    search_position: int | None = None,
    preferred_brands: list[str] | None = None,
) -> dict[str, Any]:
    product = data.get("product") or {}
    clean = clean_asin(product.get("asin") or asin)
    domain = amazon_domain or "amazon.com"
    title = str(product.get("title") or "").strip()
    brand = str(product.get("brand") or "").strip()
    product_url = str(product.get("link") or product.get("url") or _product_url(domain, clean)).strip()
    bullets = _string_list(product.get("feature_bullets") or product.get("feature_bullets_flat"))
    aplus_text = _extract_a_plus_text(product)
    original_copy = " ".join([*bullets, aplus_text]).strip()
    main_image = _extract_link(product.get("main_image"))
    media = normalize_product_media(product, clean, domain, product_url)
    has_video = any(item.get("media_type") == "video" for item in media)
    has_aplus = bool(aplus_text)

    metadata = {
        "asin": clean,
        "amazon_domain": domain,
        "rating": _to_float(product.get("rating")),
        "reviews": _to_int(product.get("ratings_total") or product.get("reviews_total")),
        "price": _extract_price(product),
        "categories": _string_list(product.get("categories")),
        "source_query": source_query,
        "search_position": search_position,
        "has_video": has_video,
        "has_a_plus_content": has_aplus,
        "media_count": len(media),
        "video_count": sum(1 for item in media if item.get("media_type") == "video"),
        "image_count": sum(1 for item in media if item.get("media_type") == "image"),
    }

    asset = {
        "id": f"rainforest:{domain}:{clean}",
        "source_type": "rainforest",
        "platform": "Amazon",
        "channel": f"Amazon / Rainforest ({domain})",
        "asin": clean,
        "amazon_domain": domain,
        "brand": brand,
        "category": category or _infer_category(title, product),
        "title": title,
        "original_copy": original_copy,
        "source_url": product_url,
        "canonical_url": product_url,
        "image_url": main_image or _first_media_thumbnail(media),
        "media": media,
        "metadata": metadata,
        "rights_status": "link_only_no_raw_video",
        "review_status": "auto_collected",
        "collected_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_payload": _compact_source_payload(product),
    }
    asset["quality_score"] = score_asset(asset, preferred_brands=preferred_brands or [])
    tags, analysis = heuristic_asset_analysis(asset)
    asset["ai_tags"] = tags
    asset["ai_analysis"] = analysis
    return asset


def normalize_product_media(product: dict[str, Any], asin: str, amazon_domain: str, product_url: str) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []

    for video in _collect_named_media(product, {"videos", "video_blocks", "image_block_videos", "customer_videos"}):
        item = _normalize_media_item(video, "video", asin, amazon_domain, product_url)
        if item:
            media.append(item)

    main_image = _normalize_media_item(product.get("main_image"), "image", asin, amazon_domain, product_url)
    if main_image:
        main_image["title"] = main_image.get("title") or "Main product image"
        media.append(main_image)

    for image in _collect_named_media(product, {"images", "image_blocks"}):
        item = _normalize_media_item(image, "image", asin, amazon_domain, product_url)
        if item:
            media.append(item)

    return _dedupe_media(media)


def score_asset(asset: dict[str, Any], *, preferred_brands: list[str] | None = None) -> int:
    metadata = asset.get("metadata") or {}
    score = 0.0
    position = _to_int(metadata.get("search_position"))
    if position:
        score += max(0, 30 - (position - 1) * 3)
    rating = _to_float(metadata.get("rating"))
    if rating:
        score += min(25, rating * 5)
    reviews = _to_int(metadata.get("reviews"))
    if reviews:
        score += min(20, math.log10(max(reviews, 1)) * 6)
    if metadata.get("has_video"):
        score += 18
    if metadata.get("has_a_plus_content"):
        score += 8
    if asset.get("image_url"):
        score += 4
    brand = str(asset.get("brand") or "").lower()
    if brand and any(brand == str(item or "").lower() for item in (preferred_brands or [])):
        score += 8
    return int(round(min(score, 100)))


def heuristic_asset_analysis(asset: dict[str, Any]) -> tuple[list[str], str]:
    text = " ".join(
        [
            str(asset.get("title") or ""),
            str(asset.get("original_copy") or ""),
            str((asset.get("metadata") or {}).get("source_query") or ""),
        ]
    ).lower()
    tag_rules = [
        ("4K", ["4k", "uhd"]),
        ("OLED/QLED", ["oled", "qled", "quantum"]),
        ("大容量", ["large capacity", "cu. ft", "family size", "extra large"]),
        ("智能互联", ["smart", "wifi", "wi-fi", "app", "alexa", "google"]),
        ("快速/省时", ["quick", "fast", "rapid", "speed"]),
        ("静音/舒适", ["quiet", "silent", "low noise"]),
        ("节能", ["energy star", "efficient", "energy"]),
        ("易清洁", ["easy clean", "self-clean", "dishwasher safe"]),
        ("健康少油", ["air fry", "less oil", "healthy"]),
        ("游戏体验", ["gaming", "144hz", "120hz", "game mode"]),
    ]
    tags = [tag for tag, needles in tag_rules if any(needle in text for needle in needles)]
    if (asset.get("metadata") or {}).get("has_video"):
        tags.append("含站内视频")
    if (asset.get("metadata") or {}).get("has_a_plus_content"):
        tags.append("含A+内容")
    if not tags:
        tags = ["Amazon站内素材", "竞品文案参考"]

    media_hint = "有站内视频，可优先作为视频表达参考" if (asset.get("metadata") or {}).get("has_video") else "暂未识别到站内视频，可作为商品页文案和图片证据"
    analysis = (
        f"{asset.get('brand') or '竞品'} 的该 Amazon 素材围绕“{asset.get('title') or '商品卖点'}”展开，"
        f"可参考其标题、五点描述、图片/A+模块中的利益点排序。{media_hint}；"
        "素材仅保存链接和结构化分析，不保存竞品原视频。"
    )
    return _unique_strings(tags)[:8], analysis


def _normalize_media_item(value: Any, media_type: str, asin: str, amazon_domain: str, product_url: str) -> dict[str, Any] | None:
    if not value:
        return None
    if isinstance(value, str):
        link = value
        raw = {"link": value}
    elif isinstance(value, dict):
        raw = value
        link = _extract_link(value)
    else:
        return None
    if not link:
        return None
    thumbnail = _extract_thumbnail(raw)
    title = str(raw.get("title") or raw.get("name") or raw.get("alt") or raw.get("variant") or "").strip()
    media_id = re.sub(r"[^A-Za-z0-9_.:-]+", "_", f"{asin}:{media_type}:{link}")[:220]
    return {
        "id": media_id,
        "asin": asin,
        "amazon_domain": amazon_domain,
        "product_url": product_url,
        "media_type": media_type,
        "media_url": link,
        "thumbnail_url": thumbnail,
        "title": title,
        "source_payload": _trim_payload(raw),
        "rights_status": "link_only_no_raw_video",
    }


def _collect_named_media(value: Any, target_keys: set[str]) -> list[Any]:
    collected: list[Any] = []

    def visit(node: Any, key_name: str = ""):
        if isinstance(node, dict):
            normalized_key = key_name.lower()
            if normalized_key in target_keys:
                if isinstance(node, dict) and _extract_link(node):
                    collected.append(node)
                else:
                    for child in _iter_list_like(node):
                        collected.append(child)
                return
            for key, child in node.items():
                visit(child, str(key))
        elif isinstance(node, list):
            if key_name.lower() in target_keys:
                collected.extend(node)
                return
            for child in node:
                visit(child, key_name)

    visit(value)
    return collected


def _dedupe_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("media_type") or ""), str(item.get("media_url") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:80]


def _extract_link(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("link", "url", "href", "media_url", "video_url", "videoUrl", "source", "src", "large", "hi_res", "hiRes"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip().startswith(("http://", "https://")):
            return candidate.strip()
        if isinstance(candidate, dict):
            nested = _extract_link(candidate)
            if nested:
                return nested
    return ""


def _extract_thumbnail(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("thumbnail", "thumbnail_url", "preview", "preview_image", "poster", "image", "thumb"):
        candidate = value.get(key)
        link = _extract_link(candidate)
        if link:
            return link
    return ""


def _first_media_thumbnail(media: list[dict[str, Any]]) -> str:
    for item in media:
        if item.get("thumbnail_url"):
            return str(item["thumbnail_url"])
        if item.get("media_type") == "image" and item.get("media_url"):
            return str(item["media_url"])
    return ""


def _extract_a_plus_text(product: dict[str, Any]) -> str:
    texts: list[str] = []
    for key in ("a_plus_content", "aplus", "a_plus", "enhanced_content", "product_description"):
        if key in product:
            texts.extend(_collect_text(product.get(key)))
    return " ".join(_unique_strings(texts))[:4000]


def _collect_text(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        cleaned = re.sub(r"\s+", " ", value).strip()
        if len(cleaned) > 2 and not cleaned.startswith(("http://", "https://")):
            out.append(cleaned)
    elif isinstance(value, dict):
        for child in value.values():
            out.extend(_collect_text(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_collect_text(child))
    return out


def _extract_price(product: dict[str, Any]) -> str:
    buybox = product.get("buybox_winner") or {}
    price = buybox.get("price") if isinstance(buybox, dict) else None
    if isinstance(price, dict):
        return str(price.get("raw") or price.get("value") or "").strip()
    for key in ("price", "price_string"):
        candidate = product.get(key)
        if isinstance(candidate, dict):
            return str(candidate.get("raw") or candidate.get("value") or "").strip()
        if isinstance(candidate, str):
            return candidate.strip()
    return ""


def _compact_source_payload(product: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "asin",
        "title",
        "brand",
        "link",
        "rating",
        "ratings_total",
        "feature_bullets",
        "main_image",
        "categories",
        "videos",
        "images",
        "a_plus_content",
    ]
    return {key: _trim_payload(product.get(key)) for key in keys if key in product}


def _trim_payload(value: Any, max_string: int = 900) -> Any:
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, dict):
        return {str(k): _trim_payload(v, max_string=max_string) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_trim_payload(item, max_string=max_string) for item in value[:40]]
    return value


def _iter_list_like(value: dict[str, Any]) -> list[Any]:
    for key in ("items", "values", "results", "videos", "images"):
        child = value.get(key)
        if isinstance(child, list):
            return child
    return list(value.values())


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return _collect_text(value)
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                result.extend(_collect_text(item))
        return _unique_strings(result)
    return []


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _to_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _product_url(domain: str, asin: str) -> str:
    clean_domain = domain or "amazon.com"
    clean = clean_asin(asin)
    return f"https://www.{clean_domain}/dp/{urllib.parse.quote(clean)}" if clean else f"https://www.{clean_domain}/"


def _infer_category(title: str, product: dict[str, Any]) -> str:
    text = f"{title} {' '.join(_string_list(product.get('categories')))}".lower()
    if "refrigerator" in text or "fridge" in text:
        return "Refrigerator"
    if "washing machine" in text or "washer" in text:
        return "Washing Machine"
    if "dishwasher" in text:
        return "Dishwasher"
    if "air fryer" in text:
        return "Air Fryer"
    if "microwave" in text:
        return "Microwave"
    if "oven" in text:
        return "Oven"
    if "tv" in text or "television" in text:
        return "TV"
    if "air conditioner" in text or "ac " in text:
        return "Air Conditioner"
    return ""
