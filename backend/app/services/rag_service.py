"""RAG service for retrieval-augmented generation."""

import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.llm_service import LLMService
from app.services.reranking_service import RerankingService
from app.services.vector_service import SearchResult, VectorService

settings = get_settings()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryItem:
    """A video summary and the source metadata used to cite it."""

    content: str
    source: dict[str, Any]


class RAGService:
    """Service for RAG operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.vector_service = VectorService(session)
        self.reranker = RerankingService()
        from app.services.llm_service import _eff
        self.llm_service = LLMService(model=_eff("answer_model", settings.answer_model) or _eff("default_model", settings.default_model))
        self.triage_llm = LLMService(model=_eff("triage_model", settings.triage_model) or _eff("default_model", settings.default_model))

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    async def _search_with_rerank(
        self,
        question: str,
        query_embedding: list[float],
        project_id: uuid.UUID,
        top_k: int,
        video_ids: list[str] | None = None,
    ) -> list[SearchResult]:
        """Hybrid search + cross-encoder reranking."""
        # 1. Fetch candidate pool via hybrid RRF
        candidates = await self.vector_service.hybrid_search(
            query_vector=query_embedding,
            query_text=question,
            project_id=str(project_id),
            limit=top_k,
            video_ids=video_ids,
            rerank_top_k=top_k,  # fetch extras internally
        )

        if not candidates:
            return []

        # 2. Cross-encoder rerank
        passages = [
            {
                "id": str(i),
                "text": r.chunk.content,
            }
            for i, r in enumerate(candidates)
        ]

        reranked = self.reranker.rerank(
            query=question,
            passages=passages,
            top_n=top_k,
        )

        # Map reranked indexes back to candidates
        reranked_results: list[SearchResult] = []
        for rr in reranked:
            idx = int(rr["id"])
            if 0 <= idx < len(candidates):
                candidate = candidates[idx]
                # Set rerank_score from cross-encoder
                candidate.rerank_score = rr.get("score")
                reranked_results.append(candidate)

        return reranked_results

    async def _get_summaries_context(
        self, project_id: uuid.UUID, video_ids: list[str] | None = None
    ) -> list[SummaryItem]:
        """Fetch all video summaries for the project to enrich RAG context."""
        from sqlalchemy import text as sa_text
        try:
            if video_ids:
                result = await self.session.execute(
                    sa_text("""
                        SELECT vs.content, COALESCE(v.title, v.youtube_video_id) as video_label,
                               v.youtube_video_id
                        FROM video_summaries vs
                        JOIN videos v ON vs.video_id = v.id
                        WHERE v.project_id = :project_id
                        AND v.youtube_video_id = ANY(:video_ids)
                        LIMIT 10
                    """),
                    {"project_id": str(project_id), "video_ids": video_ids},
                )
            else:
                result = await self.session.execute(
                    sa_text("""
                        SELECT vs.content, COALESCE(v.title, v.youtube_video_id) as video_label,
                               v.youtube_video_id
                        FROM video_summaries vs
                        JOIN videos v ON vs.video_id = v.id
                        WHERE v.project_id = :project_id
                        LIMIT 10
                    """),
                    {"project_id": str(project_id)},
                )
            rows = result.fetchall()
            if not rows:
                return []

            items = []
            for row in rows:
                content = row[0] or ""
                video_title = row[1] or row[2]
                video_id = row[2]
                items.append(
                    SummaryItem(
                        content=content,
                        source={
                            "video_id": video_id,
                            "video_title": video_title,
                            "timestamp": None,
                            "youtube_url": self._youtube_url(video_id, None),
                            "source_type": "summary",
                            "snippet": content[:200] + "...",
                            "similarity": 0.0,
                            "rerank_score": None,
                        },
                    )
                )
            return items
        except Exception as e:
            logger.warning(f"Could not fetch summaries: {e}")
            return []

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    async def query(
        self,
        question: str,
        project_id: uuid.UUID,
        top_k: int | None = None,
        history: list[dict] | None = None,
        video_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Answer a question using RAG."""
        top_k = top_k or settings.top_k_results

        search_question = await self._rewrite_query(question, history)
        query_embedding = await self.llm_service.generate_embedding(search_question)

        results = await self._search_with_rerank(
            question=search_question,
            query_embedding=query_embedding,
            project_id=project_id,
            top_k=top_k,
            video_ids=video_ids,
        )

        summaries_context = await self._get_summaries_context(project_id, video_ids)

        if not results and not summaries_context:
            return {
                "answer": "Não encontrei informações relevantes nos vídeos para responder sua pergunta.",
                "sources": [],
            }

        context = self._build_context(results, summaries_context)
        answer = await self._generate_answer(question, context, history)

        sources = self._build_sources(results, summaries_context)

        return {"answer": answer, "sources": sources}

    async def stream_query(
        self,
        question: str,
        project_id: uuid.UUID,
        top_k: int | None = None,
        history: list[dict] | None = None,
        video_ids: list[str] | None = None,
    ) -> AsyncGenerator[str | dict, None]:
        """Stream RAG query response. Yields str chunks then a final dict with sources."""
        top_k = top_k or settings.top_k_results

        search_question = await self._rewrite_query(question, history)
        if search_question != question:
            logger.info(f"Query rewritten: '{question}' → '{search_question}'")

        query_embedding = await self.llm_service.generate_embedding(search_question)
        results = await self._search_with_rerank(
            question=search_question,
            query_embedding=query_embedding,
            project_id=project_id,
            top_k=top_k,
            video_ids=video_ids,
        )

        summaries_context = await self._get_summaries_context(project_id, video_ids)

        if not results and not summaries_context:
            yield "Não encontrei informações relevantes nos vídeos para responder sua pergunta."
            return

        context = self._build_context(results, summaries_context)
        messages = self._build_messages(question, context, history)

        async for chunk in self.llm_service.stream_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        ):
            yield chunk

        # Yield sources as final item
        yield {"__sources__": self._build_sources(results, summaries_context)}

    # -----------------------------------------------------------------
    # Query rewriting
    # -----------------------------------------------------------------

    async def _rewrite_query(self, question: str, history: list[dict] | None) -> str:
        """Rewrite question using conversation history for better embedding search."""
        if not history:
            return question

        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in history[-6:]
        )

        prompt = f"""Given this conversation history:
{history_text}

And the new user question: "{question}"

Rewrite the question as a standalone, self-contained search query that includes all necessary context from the conversation. If the question is already self-contained, return it unchanged.
Return ONLY the rewritten query, nothing else."""

        try:
            import asyncio
            response = await asyncio.wait_for(
                self.triage_llm.completion(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=100,
                ),
                timeout=5.0,
            )
            rewritten = response.choices[0].message.content.strip().strip('"')
            return rewritten if rewritten else question
        except Exception as e:
            logger.warning(f"Query rewriting failed: {e}")
            return question

    # -----------------------------------------------------------------
    # Context building
    # -----------------------------------------------------------------

    def _should_include_summaries(
        self, results: list[SearchResult], summaries_context: list[SummaryItem]
    ) -> bool:
        """Use summaries when retrieval is empty or only weakly relevant."""
        if not summaries_context:
            return False

        max_similarity = max(
            (r.similarity for r in results if r.similarity is not None),
            default=0.0,
        )
        return not results or max_similarity < 0.5

    def _build_sources(
        self, results: list[SearchResult], summaries_context: list[SummaryItem]
    ) -> list[dict[str, Any]]:
        """Build source metadata in the same order as citations in the prompt."""
        sources = (
            [item.source for item in summaries_context]
            if self._should_include_summaries(results, summaries_context)
            else []
        )
        sources.extend(
            {
                "video_id": r.video_youtube_id,
                "video_title": r.video_title or r.video_youtube_id,
                "timestamp": float(r.chunk.start_time) if r.chunk.start_time is not None else None,
                "youtube_url": self._youtube_url(r.video_youtube_id, r.chunk.start_time),
                "source_type": getattr(r.chunk, "source_type", "transcript"),
                "snippet": r.chunk.content[:200] + "...",
                "similarity": float(r.similarity),
                "rerank_score": float(r.rerank_score) if r.rerank_score is not None else None,
            }
            for r in results
        )
        return sources

    def _build_context(
        self,
        results: list[SearchResult],
        summaries_context: list[SummaryItem] | str = "",
    ) -> str:
        """Build context string from search results and video summaries.

        Summaries are only injected when the highest chunk similarity is low,
        suggesting a broad question that might benefit from summary context.
        """
        context_parts = []

        if isinstance(summaries_context, str):
            summary_items: list[SummaryItem] = []
            legacy_summary_text = summaries_context
        else:
            summary_items = summaries_context
            legacy_summary_text = ""

        # Include summaries when vector/FTS retrieval is empty. Previously the
        # empty-result case used default similarity=1.0, so summaries existed
        # but were silently omitted and the LLM saw no sources.
        if self._should_include_summaries(results, summary_items):
            summary_parts = [
                f"[Fonte {index}] Resumo do vídeo '{item.source['video_title']}':\n{item.content}"
                for index, item in enumerate(summary_items, start=1)
            ]
            context_parts.append("=== Resumos dos Vídeos ===\n" + "\n\n".join(summary_parts) + "\n")
        elif legacy_summary_text and results:
            # Keep compatibility for callers that still pass a prebuilt string.
            context_parts.append(f"=== Resumos dos Vídeos ===\n{legacy_summary_text}\n")

        source_offset = (
            len(summary_items)
            if self._should_include_summaries(results, summary_items)
            else 0
        )

        for i, result in enumerate(results):
            source_number = i + 1 + source_offset
            source_type = getattr(result.chunk, "source_type", "transcript")
            timestamp = result.chunk.start_time
            video_label = result.video_title or result.video_youtube_id or "vídeo"

            if source_type == "comment":
                label = f"[Fonte {source_number}] Comentários do vídeo '{video_label}'"
            elif timestamp:
                label = f"[Fonte {source_number}] Transcrição de '{video_label}' ({int(timestamp)}s)"
            else:
                label = f"[Fonte {source_number}] Transcrição de '{video_label}'"

            context_parts.append(f"{label}:\n{result.chunk.content}\n")

        return "\n".join(context_parts)

    # -----------------------------------------------------------------
    # LLM prompt / streaming helpers
    # -----------------------------------------------------------------

    def _build_messages(
        self,
        question: str,
        context: str,
        history: list[dict] | None,
    ) -> list[dict]:
        system = (
            "Você é um assistente que responde perguntas com base em transcrições e comentários de vídeos do YouTube. "
            "Responda em português. Sempre cite as fontes usando [Fonte N] ao referenciar informações específicas. "
            "Se houver informações conflitantes entre as fontes, mencione isso explicitamente. "
            "Use somente as fontes fornecidas no contexto e nunca peça ao usuário para reenviá-las."
        )
        prompt = f"""Responda a pergunta com base nas seguintes fontes dos vídeos.

Pergunta: {question}

Fontes:
{context}

Forneça uma resposta clara e precisa. Cite as fontes usando [Fonte N]."""

        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history[-8:])
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _generate_answer(
        self,
        question: str,
        context: str,
        history: list[dict] | None,
    ) -> str:
        messages = self._build_messages(question, context, history)
        response = await self.llm_service.completion(
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )
        return response.choices[0].message.content

    def _youtube_url(self, video_id: str | None, timestamp: float | None) -> str:
        if not video_id:
            return ""
        base = f"https://www.youtube.com/watch?v={video_id}"
        if timestamp:
            return f"{base}&t={int(timestamp)}s"
        return base
