"""Search API endpoints."""

import asyncio
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.query_expansion_service import QueryExpansionService
from app.services.video_ranking_service import VideoRankingService, _is_temporal_query

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


class SearchResponse(BaseModel):
    videos: list[dict]
    total: int
    offset: int = 0
    limit: int = 50
    search_terms: list[str] = []


async def _ytdlp_search(query: str, max_results: int, dateafter: str | None = None) -> list[dict]:
    import yt_dlp

    ydl_opts: dict = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}

    # Cookies are optional, so a missing configured file must not make every
    # search fail (this is common when running the backend in Docker).
    cookies_file = os.environ.get("YTDLP_COOKIES_FILE")
    cookies_browser = os.environ.get("YTDLP_COOKIES_BROWSER")
    if cookies_file:
        cookie_path = Path(cookies_file)
        if cookie_path.is_file():
            ydl_opts["cookiefile"] = str(cookie_path)
        else:
            logger.warning("Configured YTDLP_COOKIES_FILE does not exist: %s", cookie_path)
    elif cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

    if dateafter:
        ydl_opts["dateafter"] = dateafter

    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            videos = []
            for entry in (result or {}).get("entries", []):
                if not entry:
                    continue
                thumbnail = entry.get("thumbnail") or next(
                    (t["url"] for t in reversed(entry.get("thumbnails") or []) if t.get("url")),
                    "",
                )
                videos.append({
                    "id": entry.get("id", ""),
                    "title": entry.get("title", ""),
                    "description": (entry.get("description") or "")[:500],
                    "thumbnail_url": thumbnail,
                    "channel": entry.get("channel") or entry.get("uploader") or "",
                    "duration_seconds": entry.get("duration") or 0,
                    "published_at": entry.get("upload_date") or "",
                })
            return videos

    return await asyncio.to_thread(_run)


@router.get("/search")
async def search_videos(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(12, ge=1, le=50),
) -> dict:
    """Simple yt-dlp search, no LLM processing."""
    if not q:
        raise HTTPException(status_code=400, detail="Query is required")
    try:
        videos = await _ytdlp_search(q, max_results)
        return {"videos": videos, "total": len(videos)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@router.post("/search/smart", response_model=SearchResponse)
async def smart_search(
    q: str = Query(..., description="User query"),
    mode: str = Query("smart", description="smart | direct"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> SearchResponse:
    """Smart search (LLM triage) or direct search (raw yt-dlp, paginated)."""
    if not q:
        raise HTTPException(status_code=400, detail="Query is required")

    if mode == "direct":
        return await _direct_search(q, offset, limit)

    return await _smart_search(q, offset, limit)


async def _direct_search(q: str, offset: int, limit: int) -> SearchResponse:
    """Direct mode: raw yt-dlp search, no LLM, paginated."""
    max_results = min(offset + limit, 100)  # yt-dlp practical cap
    dateafter = "20240101" if _is_temporal_query(q) else None

    all_videos = await _ytdlp_search(q, max_results, dateafter)

    # Deduplicate (single-term search, but still)
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for v in all_videos:
        if v["id"] and v["id"] not in seen_ids:
            seen_ids.add(v["id"])
            unique.append(v)

    total = len(unique)
    page = unique[offset : offset + limit]

    return SearchResponse(
        videos=page,
        total=total,
        offset=offset,
        limit=limit,
        search_terms=[],
    )


async def _smart_search(q: str, offset: int, limit: int) -> SearchResponse:
    """Smart mode: query expansion → multi-term search → LLM ranking → pre-selected."""

    from app.services.llm_service import _eff
    terms_per_lang = _eff("search_terms_per_language", settings.search_terms_per_language)
    results_per_term = _eff("search_results_per_term", settings.search_results_per_term)
    pre_select = _eff("pre_selected_count", settings.pre_selected_count)

    # 1. Expand query into multiple search terms
    expansion_service = QueryExpansionService()
    search_terms = await expansion_service.expand(q, terms_per_lang)
    logger.info(f"Expanded '{q}' into {len(search_terms)} terms: {search_terms}")

    # 2. Search each term in parallel (filter by date for temporal queries)
    dateafter = "20240101" if _is_temporal_query(q) else None
    search_tasks = [_ytdlp_search(term, results_per_term, dateafter) for term in search_terms]
    results_per_term_result = await asyncio.gather(*search_tasks, return_exceptions=True)

    # 3. Deduplicate by video id
    seen_ids: set[str] = set()
    all_videos: list[dict] = []
    for term, term_results in zip(search_terms, results_per_term_result):
        if isinstance(term_results, Exception):
            logger.warning("YouTube search failed for expanded term %r: %s", term, term_results)
            continue
        for video in term_results:
            if video["id"] and video["id"] not in seen_ids:
                seen_ids.add(video["id"])
                all_videos.append(video)

    if not all_videos:
        raise HTTPException(status_code=502, detail="No videos found for the given query.")

    # 4. LLM ranks and pre-selects
    ranking_service = VideoRankingService()
    try:
        ranked_videos = await ranking_service.rank(q, all_videos, pre_select)
    except Exception as exc:
        # A search should still be usable if an optional ranking provider is
        # unavailable or returns an unexpected payload.
        logger.warning("Video ranking failed for %r, returning raw results: %s", q, exc)
        ranked_videos = []

    # The ranking prompt may return no qualifying items. An empty search page
    # is not useful, so keep the YouTube results as a safe fallback.
    if not ranked_videos:
        ranked_videos = [
            {
                **video,
                "relevance_score": 5,
                "relevance_reason": "",
                "pre_selected": index < pre_select,
            }
            for index, video in enumerate(all_videos)
        ]

    # Smart mode returns all ranked videos (pagination less useful after LLM ranking)
    total = len(ranked_videos)
    page = ranked_videos[offset : offset + limit] if offset > 0 else ranked_videos

    return SearchResponse(
        videos=page,
        total=total,
        offset=offset,
        limit=limit,
        search_terms=search_terms,
    )
