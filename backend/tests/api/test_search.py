"""Tests for /search and /search/smart endpoints."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_llm_response

SEARCH_URL = "/api/v1/search"
SMART_URL = "/api/v1/search/smart"


def _make_video(vid_id: str = "abc", title: str = "Test Video") -> dict:
    return {
        "id": vid_id,
        "title": title,
        "description": "Some description",
        "thumbnail_url": "https://img.youtube.com/abc",
        "channel": "Test Channel",
        "duration_seconds": 600,
        "published_at": "20260101",
    }


def _patch_ytdlp(videos: list[dict]):
    """Patch _ytdlp_search in search module."""
    return patch(
        "app.api.v1.search._ytdlp_search",
        AsyncMock(return_value=videos),
    )


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------

class TestSearchVideos:
    def test_returns_200_with_results(self, client):
        videos = [_make_video("v1"), _make_video("v2")]
        with _patch_ytdlp(videos):
            response = client.get(f"{SEARCH_URL}?q=python+tutorial")
        assert response.status_code == 200

    def test_returns_videos_list(self, client):
        videos = [_make_video("v1", "Python Basics"), _make_video("v2", "Advanced Python")]
        with _patch_ytdlp(videos):
            response = client.get(f"{SEARCH_URL}?q=python")
        data = response.json()
        assert "videos" in data
        assert len(data["videos"]) == 2

    def test_returns_total_count(self, client):
        videos = [_make_video(f"v{i}") for i in range(5)]
        with _patch_ytdlp(videos):
            response = client.get(f"{SEARCH_URL}?q=test")
        assert response.json()["total"] == 5

    def test_requires_query_param(self, client):
        response = client.get(SEARCH_URL)
        assert response.status_code == 422

    def test_passes_max_results_to_yt_dlp(self, client):
        captured = {}

        async def fake_search(query, max_results, dateafter=None):
            captured["max_results"] = max_results
            return [_make_video()]

        with patch("app.api.v1.search._ytdlp_search", fake_search):
            client.get(f"{SEARCH_URL}?q=test&max_results=20")

        assert captured["max_results"] == 20

    def test_max_results_default_is_12(self, client):
        captured = {}

        async def fake_search(query, max_results, dateafter=None):
            captured["max_results"] = max_results
            return []

        with patch("app.api.v1.search._ytdlp_search", fake_search):
            client.get(f"{SEARCH_URL}?q=test")

        assert captured["max_results"] == 12

    def test_returns_500_on_yt_dlp_error(self, client):
        with patch("app.api.v1.search._ytdlp_search", AsyncMock(side_effect=Exception("yt-dlp down"))):
            response = client.get(f"{SEARCH_URL}?q=test")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /search/smart
# ---------------------------------------------------------------------------

class TestSmartSearch:
    def _setup_mocks(
        self,
        search_terms: list[str] | None = None,
        videos_per_term: list[dict] | None = None,
        ranked: list[dict] | None = None,
    ):
        """Return a dict of patches for smart search dependencies."""
        search_terms = search_terms or ["term 1 here", "term 2 here"]
        videos_per_term = videos_per_term or [_make_video("v1"), _make_video("v2")]
        ranked = ranked or [
            {**_make_video("v1"), "relevance_score": 9, "relevance_reason": "great", "pre_selected": True},
            {**_make_video("v2"), "relevance_score": 7, "relevance_reason": "ok", "pre_selected": False},
        ]
        return search_terms, videos_per_term, ranked

    def test_returns_200(self, client):
        terms, videos, ranked = self._setup_mocks()

        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", AsyncMock(return_value=videos)), \
             patch("app.api.v1.search.VideoRankingService") as MockRank:

            MockExp.return_value.expand = AsyncMock(return_value=terms)
            MockRank.return_value.rank = AsyncMock(return_value=ranked)

            response = client.post(f"{SMART_URL}?q=python+tutorial")

        assert response.status_code == 200

    def test_returns_videos_and_search_terms(self, client):
        terms = ["how to learn python", "aprenda python rápido"]
        videos = [_make_video("v1")]
        ranked = [{**_make_video("v1"), "relevance_score": 8, "relevance_reason": "r", "pre_selected": True}]

        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", AsyncMock(return_value=videos)), \
             patch("app.api.v1.search.VideoRankingService") as MockRank:

            MockExp.return_value.expand = AsyncMock(return_value=terms)
            MockRank.return_value.rank = AsyncMock(return_value=ranked)

            response = client.post(f"{SMART_URL}?q=python")

        data = response.json()
        assert "videos" in data
        assert "search_terms" in data
        assert data["search_terms"] == terms

    def test_falls_back_to_raw_results_when_ranking_returns_empty(self, client):
        terms = ["python tutorial"]
        videos = [_make_video("v1"), _make_video("v2")]

        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", AsyncMock(return_value=videos)), \
             patch("app.api.v1.search.VideoRankingService") as MockRank:

            MockExp.return_value.expand = AsyncMock(return_value=terms)
            MockRank.return_value.rank = AsyncMock(return_value=[])

            response = client.post(f"{SMART_URL}?q=python")

        data = response.json()
        assert response.status_code == 200
        assert [video["id"] for video in data["videos"]] == ["v1", "v2"]
        assert data["videos"][0]["pre_selected"] is True

    def test_deduplicates_videos_by_id(self, client):
        terms = ["term one here", "term two here"]
        # Same video from two search terms
        same_video = _make_video("v1", "Duplicate Video")

        call_count = {"n": 0}

        async def fake_search(query, max_results, dateafter=None):
            call_count["n"] += 1
            return [same_video]

        ranked = [{**same_video, "relevance_score": 8, "relevance_reason": "r", "pre_selected": True}]

        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", fake_search), \
             patch("app.api.v1.search.VideoRankingService") as MockRank:

            MockExp.return_value.expand = AsyncMock(return_value=terms)
            MockRank.return_value.rank = AsyncMock(return_value=ranked)

            response = client.post(f"{SMART_URL}?q=python")

        data = response.json()
        # ranking service should receive deduplicated list (1 video, not 2)
        rank_call_args = MockRank.return_value.rank.call_args
        videos_passed = rank_call_args.args[1]
        assert len(videos_passed) == 1

    def test_returns_502_when_no_videos_found(self, client):
        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", AsyncMock(return_value=[])):

            MockExp.return_value.expand = AsyncMock(return_value=["term one two three"])
            response = client.post(f"{SMART_URL}?q=xyzxyzxyz")

        assert response.status_code == 502

    def test_temporal_query_uses_dateafter(self, client):
        captured = {}

        async def fake_search(query, max_results, dateafter=None):
            captured["dateafter"] = dateafter
            return [_make_video()]

        ranked = [{**_make_video(), "relevance_score": 8, "relevance_reason": "r", "pre_selected": True}]

        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", fake_search), \
             patch("app.api.v1.search.VideoRankingService") as MockRank:

            MockExp.return_value.expand = AsyncMock(return_value=["melhores SUV 2026"])
            MockRank.return_value.rank = AsyncMock(return_value=ranked)
            client.post(f"{SMART_URL}?q=melhores+SUV+2026")

        assert captured.get("dateafter") == "20240101"

    def test_non_temporal_query_no_dateafter(self, client):
        captured = {}

        async def fake_search(query, max_results, dateafter=None):
            captured["dateafter"] = dateafter
            return [_make_video()]

        ranked = [{**_make_video(), "relevance_score": 8, "relevance_reason": "r", "pre_selected": True}]

        with patch("app.api.v1.search.QueryExpansionService") as MockExp, \
             patch("app.api.v1.search._ytdlp_search", fake_search), \
             patch("app.api.v1.search.VideoRankingService") as MockRank:

            MockExp.return_value.expand = AsyncMock(return_value=["como aprender inglês"])
            MockRank.return_value.rank = AsyncMock(return_value=ranked)
            client.post(f"{SMART_URL}?q=como+aprender+ingles")

        assert captured.get("dateafter") is None

    def test_requires_query_param(self, client):
        response = client.post(SMART_URL)
        assert response.status_code == 422
