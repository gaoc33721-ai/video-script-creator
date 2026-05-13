import datetime as dt
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


YOUTUBE_API_ENDPOINT = "https://www.googleapis.com/youtube/v3"
DEFAULT_TIMEOUT = 15


class SocialApiError(RuntimeError):
    pass


def detect_platform(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if host in {"youtu.be", "youtube.com", "youtube-nocookie.com"} or host.endswith(".youtube.com"):
        return "YouTube"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "Instagram"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "TikTok"
    if host == "pinterest.com" or host.endswith(".pinterest.com") or host == "pin.it":
        return "Pinterest"
    if host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.watch":
        return "Facebook"
    return "Social"


def normalize_social_url(
    url: str,
    *,
    category: str = "",
    brands: list[str] | None = None,
    fetch_oembed: bool = True,
    oembed_access_token: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    source_url = _clean_url(url)
    if not source_url:
        raise SocialApiError("URL is required.")
    platform = detect_platform(source_url)
    content_id = extract_content_id(platform, source_url)
    canonical_url = canonical_social_url(platform, source_url, content_id)
    oembed: dict[str, Any] = {}
    oembed_error = ""
    if fetch_oembed:
        try:
            oembed = fetch_oembed_data(platform, canonical_url, access_token=oembed_access_token, timeout=timeout)
        except Exception as exc:
            oembed_error = str(exc)

    return build_social_asset(
        platform=platform,
        source_url=source_url,
        canonical_url=canonical_url,
        content_id=content_id,
        category=category,
        brands=brands or [],
        oembed=oembed,
        oembed_error=oembed_error,
    )


def discover_youtube_videos(
    api_key: str,
    *,
    category: str = "",
    brands: list[str] | None = None,
    keywords: list[str] | None = None,
    target_market: str = "",
    region_code: str = "",
    max_results: int = 8,
    request_limit: int = 4,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    if not api_key:
        raise SocialApiError("YOUTUBE_API_KEY is required.")
    queries = build_youtube_queries(category=category, brands=brands or [], keywords=keywords or [])
    queries = queries[: max(1, int(request_limit or 1))]
    if not queries:
        raise SocialApiError("Please provide category, brands, or keywords for YouTube discovery.")

    region = normalize_region_code(region_code, target_market)
    seen: dict[str, dict[str, Any]] = {}
    request_logs = []
    per_query = max(1, min(int(max_results or 8), 25))
    for query in queries:
        params = {
            "part": "snippet",
            "type": "video",
            "maxResults": str(per_query),
            "q": query,
            "order": "relevance",
            "safeSearch": "none",
            "videoEmbeddable": "true",
            "key": api_key,
        }
        if region:
            params["regionCode"] = region
        data = fetch_json(f"{YOUTUBE_API_ENDPOINT}/search", params=params, timeout=timeout)
        request_logs.append({"query": query, "result_count": len(data.get("items") or [])})
        for index, item in enumerate(data.get("items") or [], start=1):
            video_id = str(((item.get("id") or {}).get("videoId")) or "").strip()
            if not video_id or video_id in seen:
                continue
            seen[video_id] = {"video_id": video_id, "source_query": query, "position": index, "search_item": item}

    details = fetch_youtube_videos(api_key, list(seen.keys()), timeout=timeout)
    assets = []
    for detail in details:
        video_id = str(detail.get("id") or "")
        source = seen.get(video_id, {})
        assets.append(
            normalize_youtube_video_item(
                detail,
                category=category,
                preferred_brands=brands or [],
                source_query=source.get("source_query") or "",
                search_position=source.get("position"),
            )
        )
    assets.sort(key=lambda item: int(item.get("quality_score") or 0), reverse=True)
    return {
        "platform": "YouTube",
        "region_code": region,
        "queries": request_logs,
        "video_ids": list(seen.keys()),
        "assets": assets,
    }


def fetch_youtube_videos(api_key: str, video_ids: list[str], *, timeout: int = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    clean_ids = []
    for value in video_ids or []:
        video_id = extract_youtube_video_id(str(value or "")) or str(value or "").strip()
        if video_id and video_id not in clean_ids:
            clean_ids.append(video_id)
    if not clean_ids:
        return []
    if not api_key:
        raise SocialApiError("YOUTUBE_API_KEY is required.")
    items: list[dict[str, Any]] = []
    for chunk in _chunks(clean_ids, 50):
        data = fetch_json(
            f"{YOUTUBE_API_ENDPOINT}/videos",
            params={
                "part": "snippet,statistics,contentDetails,status",
                "id": ",".join(chunk),
                "key": api_key,
            },
            timeout=timeout,
        )
        items.extend(data.get("items") or [])
    return items


def normalize_youtube_video_item(
    item: dict[str, Any],
    *,
    category: str = "",
    preferred_brands: list[str] | None = None,
    source_query: str = "",
    search_position: int | None = None,
) -> dict[str, Any]:
    video_id = str(item.get("id") or "").strip()
    if not video_id:
        raise SocialApiError("YouTube video id is missing.")
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    content_details = item.get("contentDetails") or {}
    status = item.get("status") or {}
    title = str(snippet.get("title") or f"YouTube video {video_id}")
    description = str(snippet.get("description") or "")
    channel_title = str(snippet.get("channelTitle") or "")
    thumbnail = pick_youtube_thumbnail(snippet.get("thumbnails") or {})
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    embed_url = f"https://www.youtube.com/embed/{video_id}"
    now = utc_now()
    brand = _match_brand(title, description, channel_title, preferred_brands or []) or channel_title
    tags = ["YouTube", "视频素材", "官方API"]
    if source_query:
        tags.append(f"关键词:{source_query[:32]}")
    score = score_youtube_asset(statistics=statistics, thumbnail=thumbnail, title=title, preferred_brand=brand)
    metadata = {
        "platform_content_id": video_id,
        "youtube_video_id": video_id,
        "youtube_channel_id": snippet.get("channelId") or "",
        "account_name": channel_title,
        "published_at": snippet.get("publishedAt") or "",
        "duration": content_details.get("duration") or "",
        "source_query": source_query,
        "search_position": search_position,
        "captured_at": now,
        "thumbnail_expires_at": "",
        "engagement_snapshot": {
            "views": _to_int(statistics.get("viewCount")),
            "likes": _to_int(statistics.get("likeCount")),
            "comments": _to_int(statistics.get("commentCount")),
            "captured_at": now,
        },
        "embeddable": status.get("embeddable"),
        "privacy_status": status.get("privacyStatus") or "",
        "video_count": 1,
        "has_video": True,
        "has_raw_video": False,
        "rights_status": "link_only_no_raw_video",
    }
    return {
        "id": f"youtube:{video_id}",
        "source_type": "youtube_api",
        "platform": "YouTube",
        "channel": channel_title or "YouTube",
        "brand": brand,
        "category": category,
        "asin": "",
        "amazon_domain": "",
        "title": title,
        "original_copy": description[:1800],
        "source_url": source_url,
        "canonical_url": source_url,
        "embed_url": embed_url,
        "embed_html": "",
        "image_url": thumbnail,
        "media": [
            {
                "id": f"youtube:{video_id}:video",
                "brand": brand,
                "category": category,
                "product_url": source_url,
                "media_type": "video",
                "media_url": source_url,
                "thumbnail_url": thumbnail,
                "title": title,
                "rights_status": "link_only_no_raw_video",
                "source_payload": {
                    "platform": "YouTube",
                    "platform_content_id": video_id,
                    "embed_url": embed_url,
                    "captured_at": now,
                },
            }
        ],
        "ai_tags": tags,
        "ai_analysis": build_social_analysis(platform="YouTube", title=title, caption=description, media_type="video"),
        "quality_score": score,
        "rights_status": "link_only_no_raw_video",
        "review_status": "auto_collected",
        "metadata": metadata,
        "source_payload": {"api": "youtube_data_api", "raw": item},
        "collected_at": now,
        "created_at": now,
        "updated_at": now,
    }


def refresh_social_thumbnail(
    asset: dict[str, Any],
    *,
    youtube_api_key: str = "",
    oembed_access_token: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[dict[str, Any], bool, str]:
    platform = str(asset.get("platform") or "").strip() or detect_platform(asset.get("source_url") or "")
    source_url = str(asset.get("source_url") or asset.get("canonical_url") or "").strip()
    metadata = dict(asset.get("metadata") or {})
    content_id = str(metadata.get("platform_content_id") or metadata.get("youtube_video_id") or "").strip()
    if platform == "YouTube":
        video_id = content_id or extract_youtube_video_id(source_url)
        if youtube_api_key and video_id:
            items = fetch_youtube_videos(youtube_api_key, [video_id], timeout=timeout)
            if items:
                refreshed = normalize_youtube_video_item(
                    items[0],
                    category=asset.get("category") or "",
                    preferred_brands=[asset.get("brand") or ""],
                    source_query=metadata.get("source_query") or "",
                    search_position=metadata.get("search_position"),
                )
                merged = {**asset, **refreshed}
                merged["created_at"] = asset.get("created_at") or refreshed.get("created_at")
                return merged, True, ""
        if video_id:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            return _apply_thumbnail(asset, thumbnail), True, ""

    if not source_url:
        return asset, False, "missing source_url"
    try:
        oembed = fetch_oembed_data(platform, source_url, access_token=oembed_access_token, timeout=timeout)
    except Exception as exc:
        return asset, False, str(exc)
    thumbnail = str(oembed.get("thumbnail_url") or "")
    if not thumbnail:
        return asset, False, "oEmbed response did not include thumbnail_url"
    refreshed = _apply_thumbnail(asset, thumbnail)
    refreshed["embed_html"] = oembed.get("html") or refreshed.get("embed_html") or ""
    if oembed.get("title") and not refreshed.get("title"):
        refreshed["title"] = oembed.get("title")
    refreshed["metadata"] = {**metadata, "thumbnail_refreshed_at": utc_now(), "oembed_provider": oembed.get("provider_name") or ""}
    return refreshed, True, ""


def build_social_asset(
    *,
    platform: str,
    source_url: str,
    canonical_url: str,
    content_id: str,
    category: str,
    brands: list[str],
    oembed: dict[str, Any] | None = None,
    oembed_error: str = "",
) -> dict[str, Any]:
    oembed = oembed or {}
    now = utc_now()
    platform_key = platform.lower()
    title = str(oembed.get("title") or _default_social_title(platform, content_id))
    caption = str(oembed.get("description") or "")
    account_name = str(oembed.get("author_name") or "")
    media_type = infer_media_type(platform, canonical_url, oembed)
    thumbnail = str(oembed.get("thumbnail_url") or "")
    if not thumbnail and platform == "YouTube" and content_id:
        thumbnail = f"https://i.ytimg.com/vi/{content_id}/hqdefault.jpg"
    brand = _match_brand(title, caption, account_name, brands) or (brands[0] if brands else account_name)
    source_type = f"{platform_key}_oembed" if oembed else f"{platform_key}_manual"
    if platform == "Instagram" and not oembed:
        source_type = "instagram_manual"
    embed_url = f"https://www.youtube.com/embed/{content_id}" if platform == "YouTube" and content_id else ""
    metadata = {
        "platform_content_id": content_id,
        "account_name": account_name,
        "author_url": oembed.get("author_url") or "",
        "provider_name": oembed.get("provider_name") or platform,
        "provider_url": oembed.get("provider_url") or "",
        "asset_type": media_type,
        "collected_method": "manual_url_oembed" if oembed else "manual_url",
        "oembed_fetch_error": oembed_error,
        "thumbnail_expires_at": "",
        "captured_at": now,
        "video_count": 1 if media_type == "video" else 0,
        "post_count": 0 if media_type == "video" else 1,
        "has_video": media_type == "video",
        "has_raw_video": False,
        "rights_status": "link_only_no_raw_video",
    }
    asset_id = f"{platform_key}:{content_id or _stable_id(canonical_url)}"
    return {
        "id": asset_id,
        "source_type": source_type,
        "platform": platform,
        "channel": account_name or platform,
        "brand": brand,
        "category": category,
        "asin": "",
        "amazon_domain": "",
        "title": title,
        "original_copy": caption,
        "source_url": canonical_url,
        "canonical_url": canonical_url,
        "embed_url": embed_url,
        "embed_html": oembed.get("html") or "",
        "image_url": thumbnail,
        "media": [
            {
                "id": f"{asset_id}:{media_type}",
                "brand": brand,
                "category": category,
                "product_url": canonical_url,
                "media_type": media_type,
                "media_url": canonical_url,
                "thumbnail_url": thumbnail,
                "title": title,
                "rights_status": "link_only_no_raw_video",
                "source_payload": {
                    "platform": platform,
                    "platform_content_id": content_id,
                    "embed_url": embed_url,
                    "collected_method": metadata["collected_method"],
                },
            }
        ],
        "ai_tags": [platform, "社媒素材", "URL入库", "视频素材" if media_type == "video" else "社媒帖"],
        "ai_analysis": build_social_analysis(platform=platform, title=title, caption=caption, media_type=media_type),
        "quality_score": 72 if thumbnail else 62,
        "rights_status": "link_only_no_raw_video",
        "review_status": "manual_url",
        "metadata": metadata,
        "source_payload": {"oembed": oembed} if oembed else {"note": "Raw social media was not downloaded."},
        "collected_at": now,
        "created_at": now,
        "updated_at": now,
    }


def fetch_oembed_data(platform: str, url: str, *, access_token: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    if platform == "YouTube":
        return fetch_json("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=timeout)
    if platform == "TikTok":
        return fetch_json("https://www.tiktok.com/oembed", params={"url": url}, timeout=timeout)
    if platform == "Pinterest":
        return fetch_json("https://www.pinterest.com/oembed.json", params={"url": url}, timeout=timeout)
    if platform == "Instagram":
        if not access_token:
            raise SocialApiError("Instagram oEmbed requires SOCIAL_OEMBED_ACCESS_TOKEN.")
        return fetch_json(
            "https://graph.facebook.com/v20.0/instagram_oembed",
            params={"url": url, "access_token": access_token},
            timeout=timeout,
        )
    if platform == "Facebook":
        if not access_token:
            raise SocialApiError("Facebook oEmbed requires SOCIAL_OEMBED_ACCESS_TOKEN.")
        endpoint = "oembed_video" if re.search(r"/(watch|videos|reel|share/v)/", url) else "oembed_post"
        return fetch_json(
            f"https://graph.facebook.com/v20.0/{endpoint}",
            params={"url": url, "access_token": access_token},
            timeout=timeout,
        )
    raise SocialApiError(f"{platform} oEmbed is not configured.")


def fetch_json(url: str, *, params: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "competitor-social-ingestion/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise SocialApiError(f"HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise SocialApiError(str(exc)) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SocialApiError("Response was not valid JSON.") from exc
    if isinstance(data, dict) and data.get("error"):
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise SocialApiError(message or "Social API returned an error.")
    return data if isinstance(data, dict) else {}


def build_youtube_queries(*, category: str, brands: list[str], keywords: list[str]) -> list[str]:
    base_terms = [item.strip() for item in keywords if item and item.strip()]
    if category and category.strip():
        base_terms.append(category.strip())
    if not base_terms:
        base_terms = ["product demo"]
    queries = []
    clean_brands = [item.strip() for item in brands if item and item.strip()]
    if clean_brands:
        for brand in clean_brands:
            for term in base_terms:
                queries.append(f"{brand} {term} video")
    else:
        queries.extend(f"{term} product video" for term in base_terms)
    return _dedupe(queries)


def extract_content_id(platform: str, url: str) -> str:
    if platform == "YouTube":
        return extract_youtube_video_id(url)
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if platform == "Instagram":
        for marker in ("reel", "p", "tv"):
            if marker in parts:
                index = parts.index(marker)
                if len(parts) > index + 1:
                    return parts[index + 1]
    if platform == "TikTok":
        match = re.search(r"/video/(\d+)", parsed.path)
        if match:
            return match.group(1)
    if platform == "Pinterest":
        match = re.search(r"/pin/(\d+)", parsed.path)
        if match:
            return match.group(1)
    if platform == "Facebook":
        for pattern in (r"/videos/(\d+)", r"/reel/(\d+)", r"/watch/\?v=(\d+)"):
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("v"):
            return query["v"][0]
    return _stable_id(url)


def extract_youtube_video_id(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower().replace("www.", "").replace("m.", "")
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0][:11]
    parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be" and parts:
        return parts[0][:11]
    for marker in ("shorts", "embed", "live", "v"):
        if marker in parts:
            index = parts.index(marker)
            if len(parts) > index + 1:
                return parts[index + 1][:11]
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", text)
    return match.group(1) if match else ""


def canonical_social_url(platform: str, source_url: str, content_id: str) -> str:
    if platform == "YouTube" and content_id:
        return f"https://www.youtube.com/watch?v={content_id}"
    if platform == "Instagram" and content_id:
        path = urllib.parse.urlparse(source_url).path
        media_type = "reel" if "/reel/" in path else "p"
        return f"https://www.instagram.com/{media_type}/{content_id}/"
    return source_url


def infer_media_type(platform: str, url: str, oembed: dict[str, Any]) -> str:
    if str(oembed.get("type") or "").lower() == "video":
        return "video"
    if platform in {"YouTube", "TikTok"}:
        return "video"
    if platform == "Instagram" and re.search(r"/(reel|tv)/", urllib.parse.urlparse(url).path):
        return "video"
    if platform == "Facebook" and re.search(r"/(watch|videos|reel|share/v)/", url):
        return "video"
    return "post"


def pick_youtube_thumbnail(thumbnails: dict[str, Any]) -> str:
    for key in ("maxres", "standard", "high", "medium", "default"):
        value = thumbnails.get(key) or {}
        if isinstance(value, dict) and value.get("url"):
            return str(value["url"])
    return ""


def score_youtube_asset(*, statistics: dict[str, Any], thumbnail: str, title: str, preferred_brand: str) -> int:
    views = _to_int(statistics.get("viewCount"))
    comments = _to_int(statistics.get("commentCount"))
    likes = _to_int(statistics.get("likeCount"))
    score = 58
    if thumbnail:
        score += 8
    if title:
        score += 4
    if preferred_brand:
        score += 4
    if views >= 100000:
        score += 14
    elif views >= 10000:
        score += 9
    elif views >= 1000:
        score += 5
    if comments >= 100:
        score += 5
    elif comments >= 10:
        score += 3
    if likes >= 1000:
        score += 5
    elif likes >= 100:
        score += 3
    return max(0, min(score, 100))


def build_social_analysis(*, platform: str, title: str, caption: str, media_type: str) -> str:
    title_hint = title or "未命名素材"
    copy_hint = "标题/描述" if caption else "标题和缩略图"
    return (
        f"该{platform}{'视频' if media_type == 'video' else '帖子'}素材围绕“{title_hint}”展开，"
        f"当前阶段基于{copy_hint}、缩略图和平台链接做结构化分析，未下载或保存竞品原片。"
        "可重点拆解开场 Hook、场景设置、卖点表达、镜头节奏和结尾 CTA；"
        "报告引用时必须回指原始链接、平台内容 ID 和抓取时间。"
    )


def normalize_region_code(region_code: str, target_market: str) -> str:
    value = str(region_code or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", value):
        return value
    text = str(target_market or "").lower()
    if "uk" in text or "英国" in text:
        return "GB"
    if "de" in text or "德国" in text:
        return "DE"
    if "fr" in text or "法国" in text:
        return "FR"
    if "ca" in text or "加拿大" in text:
        return "CA"
    return "US" if text else ""


def _apply_thumbnail(asset: dict[str, Any], thumbnail: str) -> dict[str, Any]:
    refreshed = dict(asset)
    refreshed["image_url"] = thumbnail
    media = []
    for item in refreshed.get("media", []) or []:
        media_item = dict(item or {})
        media_item["thumbnail_url"] = thumbnail
        media.append(media_item)
    refreshed["media"] = media
    metadata = dict(refreshed.get("metadata") or {})
    metadata["thumbnail_refreshed_at"] = utc_now()
    refreshed["metadata"] = metadata
    refreshed["updated_at"] = utc_now()
    return refreshed


def _match_brand(title: str, caption: str, account_name: str, brands: list[str]) -> str:
    haystack = f"{title} {caption} {account_name}".lower()
    for brand in brands:
        clean = str(brand or "").strip()
        if clean and clean.lower() in haystack:
            return clean
    return ""


def _default_social_title(platform: str, content_id: str) -> str:
    return f"{platform} social asset {content_id}" if content_id else f"{platform} social asset"


def _clean_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, flags=re.I):
        text = "https://" + text
    return text


def _stable_id(value: str) -> str:
    return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:16]


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(clean)
    return result


def utc_now() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
