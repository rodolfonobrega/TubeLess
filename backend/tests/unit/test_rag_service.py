"""Tests for RAGService — pure methods and mocked dependencies."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rag_service import RAGService, SummaryItem
from app.services.vector_service import SearchResult
from tests.conftest import make_llm_response


def _make_chunk(content: str, start_time: float | None = None, source_type: str = "transcript"):
    chunk = MagicMock()
    chunk.content = content
    chunk.start_time = start_time
    chunk.source_type = source_type
    return chunk


def _make_search_result(
    content: str,
    video_title: str = "Test Video",
    video_youtube_id: str = "abc123",
    start_time: float | None = 30.0,
    similarity: float = 0.9,
    source_type: str = "transcript",
) -> SearchResult:
    chunk = _make_chunk(content, start_time, source_type)
    return SearchResult(
        chunk=chunk,
        similarity=similarity,
        video_title=video_title,
        video_youtube_id=video_youtube_id,
    )


def _make_rag_service() -> RAGService:
    """Build RAGService with all external deps mocked."""
    mock_session = AsyncMock()
    with patch("app.services.rag_service.VectorService"), \
         patch("app.services.rag_service.LLMService"):
        svc = RAGService(mock_session)
        svc.llm_service = MagicMock()
        svc.triage_llm = MagicMock()
        svc.vector_service = MagicMock()
        return svc


# ---------------------------------------------------------------------------
# _youtube_url
# ---------------------------------------------------------------------------

class TestYoutubeUrl:
    def test_url_with_timestamp(self):
        svc = _make_rag_service()
        url = svc._youtube_url("dQw4w9WgXcQ", 42.5)
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"

    def test_url_without_timestamp(self):
        svc = _make_rag_service()
        url = svc._youtube_url("dQw4w9WgXcQ", None)
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_url_with_zero_timestamp_omitted(self):
        svc = _make_rag_service()
        url = svc._youtube_url("abc", 0.0)
        # 0.0 is falsy → no timestamp appended
        assert "&t=" not in url

    def test_no_video_id_returns_empty(self):
        svc = _make_rag_service()
        assert svc._youtube_url(None, 30.0) == ""
        assert svc._youtube_url("", 30.0) == ""


# ---------------------------------------------------------------------------
# _build_context
# ---------------------------------------------------------------------------

class TestBuildContext:
    def test_transcript_with_timestamp(self):
        svc = _make_rag_service()
        results = [_make_search_result("Content here", start_time=120.0, source_type="transcript")]
        ctx = svc._build_context(results)
        assert "120" in ctx
        assert "Test Video" in ctx
        assert "Content here" in ctx

    def test_comment_source_label(self):
        svc = _make_rag_service()
        results = [_make_search_result("Great comment text", start_time=None, source_type="comment")]
        ctx = svc._build_context(results)
        assert "Comentários" in ctx
        assert "Great comment text" in ctx

    def test_transcript_without_timestamp(self):
        svc = _make_rag_service()
        results = [_make_search_result("Content", start_time=None, source_type="transcript")]
        ctx = svc._build_context(results)
        assert "Test Video" in ctx
        # No timestamp → no seconds marker
        assert "(None" not in ctx

    def test_multiple_results_numbered(self):
        svc = _make_rag_service()
        results = [
            _make_search_result("First content", video_title="Video A"),
            _make_search_result("Second content", video_title="Video B"),
        ]
        ctx = svc._build_context(results)
        assert "[Fonte 1]" in ctx
        assert "[Fonte 2]" in ctx

    def test_empty_results_empty_context(self):
        svc = _make_rag_service()
        ctx = svc._build_context([])
        assert ctx == ""

    def test_summary_fallback_is_numbered_as_a_source(self):
        svc = _make_rag_service()
        summaries = [
            SummaryItem(
                content="Confira paredes, teto e instalações.",
                source={"video_title": "Vistoria do apartamento"},
            )
        ]

        ctx = svc._build_context([], summaries)

        assert "[Fonte 1]" in ctx
        assert "Vistoria do apartamento" in ctx
        assert "instalações" in ctx


# ---------------------------------------------------------------------------
# _rewrite_query
# ---------------------------------------------------------------------------

class TestRewriteQuery:
    @pytest.mark.asyncio
    async def test_returns_original_when_no_history(self):
        svc = _make_rag_service()
        result = await svc._rewrite_query("original question", None)
        assert result == "original question"
        svc.triage_llm.completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_rewrites_with_history(self):
        svc = _make_rag_service()
        svc.triage_llm.completion = AsyncMock(
            return_value=make_llm_response("consumo de combustível dos melhores SUVs 2026")
        )
        history = [
            {"role": "user", "content": "quais os melhores SUVs 2026?"},
            {"role": "assistant", "content": "Os melhores são..."},
        ]
        result = await svc._rewrite_query("e o consumo?", history)
        assert result == "consumo de combustível dos melhores SUVs 2026"
        svc.triage_llm.completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_triage_llm_not_answer_llm(self):
        svc = _make_rag_service()
        svc.triage_llm.completion = AsyncMock(return_value=make_llm_response("rewritten"))
        svc.llm_service.completion = AsyncMock()

        history = [{"role": "user", "content": "previous question here"}]
        await svc._rewrite_query("follow up", history)

        svc.triage_llm.completion.assert_called_once()
        svc.llm_service.completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_to_original_on_error(self):
        svc = _make_rag_service()
        svc.triage_llm.completion = AsyncMock(side_effect=Exception("LLM error"))
        history = [{"role": "user", "content": "something"}]
        result = await svc._rewrite_query("original", history)
        assert result == "original"

    @pytest.mark.asyncio
    async def test_strips_quotes_from_rewritten(self):
        svc = _make_rag_service()
        svc.triage_llm.completion = AsyncMock(
            return_value=make_llm_response('"rewritten query without quotes"')
        )
        history = [{"role": "user", "content": "something"}]
        result = await svc._rewrite_query("q", history)
        assert not result.startswith('"')
        assert not result.endswith('"')


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_includes_system_message(self):
        svc = _make_rag_service()
        messages = svc._build_messages("question", "context", None)
        assert messages[0]["role"] == "system"

    def test_last_message_is_user(self):
        svc = _make_rag_service()
        messages = svc._build_messages("question", "context", None)
        assert messages[-1]["role"] == "user"

    def test_history_included_between_system_and_user(self):
        svc = _make_rag_service()
        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        messages = svc._build_messages("new question", "context", history)
        roles = [m["role"] for m in messages]
        assert roles[0] == "system"
        assert "user" in roles[1:-1] or "assistant" in roles[1:-1]
        assert roles[-1] == "user"

    def test_question_in_user_message(self):
        svc = _make_rag_service()
        messages = svc._build_messages("What is X?", "some context", None)
        user_content = messages[-1]["content"]
        assert "What is X?" in user_content

    def test_context_in_user_message(self):
        svc = _make_rag_service()
        messages = svc._build_messages("question", "special context marker", None)
        user_content = messages[-1]["content"]
        assert "special context marker" in user_content

    def test_limits_history_to_last_8(self):
        svc = _make_rag_service()
        history = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        messages = svc._build_messages("q", "ctx", history)
        # system + up to 8 history + user = at most 10
        assert len(messages) <= 10


# ---------------------------------------------------------------------------
# query — end-to-end with mocks
# ---------------------------------------------------------------------------

class TestQuery:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_no_results(self):
        svc = _make_rag_service()
        svc.vector_service.hybrid_search = AsyncMock(return_value=[])
        svc.llm_service.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc.triage_llm.completion = AsyncMock(return_value=make_llm_response(""))

        result = await svc.query("question", project_id=__import__("uuid").uuid4())
        assert "answer" in result
        assert result["sources"] == []

    @pytest.mark.asyncio
    async def test_returns_answer_and_sources(self):
        svc = _make_rag_service()
        results = [_make_search_result("Relevant content here")]
        svc.vector_service.hybrid_search = AsyncMock(return_value=results)
        svc.llm_service.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc.llm_service.completion = AsyncMock(return_value=make_llm_response("The answer is X."))
        svc.triage_llm.completion = AsyncMock(return_value=make_llm_response("question"))

        import uuid
        result = await svc.query("question", project_id=uuid.uuid4())
        assert result["answer"] == "The answer is X."
        assert len(result["sources"]) == 1
        assert result["sources"][0]["video_title"] == "Test Video"

    @pytest.mark.asyncio
    async def test_uses_summary_as_source_when_retrieval_is_empty(self):
        svc = _make_rag_service()
        svc.vector_service.hybrid_search = AsyncMock(return_value=[])
        svc.llm_service.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc.llm_service.completion = AsyncMock(return_value=make_llm_response("Resposta [Fonte 1]."))
        svc._get_summaries_context = AsyncMock(
            return_value=[
                SummaryItem(
                    content="Confira paredes, teto e instalações.",
                    source={
                        "video_id": "abc123",
                        "video_title": "Vistoria do apartamento",
                        "timestamp": None,
                        "youtube_url": "https://www.youtube.com/watch?v=abc123",
                        "source_type": "summary",
                        "snippet": "Confira paredes, teto e instalações.",
                        "similarity": 0.0,
                        "rerank_score": None,
                    },
                )
            ]
        )

        import uuid
        result = await svc.query("question", project_id=uuid.uuid4())

        assert result["answer"] == "Resposta [Fonte 1]."
        assert len(result["sources"]) == 1
        assert result["sources"][0]["source_type"] == "summary"

    @pytest.mark.asyncio
    async def test_sources_include_youtube_url(self):
        svc = _make_rag_service()
        results = [_make_search_result("Content", video_youtube_id="xyz789", start_time=60.0)]
        svc.vector_service.hybrid_search = AsyncMock(return_value=results)
        svc.llm_service.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc.llm_service.completion = AsyncMock(return_value=make_llm_response("answer"))
        svc.triage_llm.completion = AsyncMock(return_value=make_llm_response("question"))

        import uuid
        result = await svc.query("question", project_id=uuid.uuid4())
        url = result["sources"][0]["youtube_url"]
        assert "xyz789" in url
        assert "t=60s" in url

    @pytest.mark.asyncio
    async def test_sources_include_source_type(self):
        svc = _make_rag_service()
        results = [_make_search_result("Comment content", source_type="comment")]
        svc.vector_service.hybrid_search = AsyncMock(return_value=results)
        svc.llm_service.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc.llm_service.completion = AsyncMock(return_value=make_llm_response("answer"))
        svc.triage_llm.completion = AsyncMock(return_value=make_llm_response("question"))

        import uuid
        result = await svc.query("question", project_id=uuid.uuid4())
        assert result["sources"][0]["source_type"] == "comment"
