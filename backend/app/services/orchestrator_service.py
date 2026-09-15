import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.orm import (
    ConsolidatedSummary,
    Project,
    ProjectStatus,
    TranscriptChunk,
    Video,
    VideoSummary,
)
from app.services.chunking_service import ChunkingService
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.summarization_service import SummarizationService
from app.services.transcript_service import TranscriptService
from app.services.vector_service import VectorService

settings = get_settings()
logger = logging.getLogger(__name__)


class ProcessingOrchestrator:
    """Orchestrates the full video processing pipeline."""

    def __init__(self, session: AsyncSession, ws_manager) -> None:
        self.session = session
        self.ws_manager = ws_manager
        self.transcript_service = TranscriptService()
        self.chunking_service = ChunkingService()
        self.summarization_service = SummarizationService()
        self.llm_service = LLMService()
        self.embedding_service = EmbeddingService(session)
        self.vector_service = VectorService(session)

    async def process_project(self, project_id: uuid.UUID) -> None:
        """Process a project through the full pipeline.

        On retry, videos that already have a downloaded transcript reuse it —
        only the summarization step is re-run. Videos without a transcript
        go through the full download pipeline.
        """
        import json as _json
        from app.models.orm import Transcript
        from app.services.comment_service import CommentService

        try:
            while True:
                result = await self.session.execute(select(Project).where(Project.id == project_id))
                project = result.scalar_one_or_none()
                if not project:
                    raise ValueError(f"Project {project_id} not found")

                videos_result = await self.session.execute(
                    select(Video)
                    .where(Video.project_id == project_id)
                    .options(
                        selectinload(Video.transcript).selectinload(Transcript.chunks),
                        selectinload(Video.summary),
                    )
                    .order_by(Video.created_at)
                )
                videos = list(videos_result.scalars().all())
                if not videos:
                    raise ValueError("No videos in project")

                pending_videos = [v for v in videos if v.status in ("pending", "processing")]

                if pending_videos:
                    # Fetch metadata for videos that don't have a title yet to show titles in the queue UI
                    videos_without_title = [v for v in pending_videos if not v.title]
                    if videos_without_title:
                        await self._fetch_videos_metadata(videos_without_title)
                        await self.session.commit()
                        
                        # Re-fetch videos from DB to prevent expired attributes issues after commit
                        videos_result = await self.session.execute(
                            select(Video)
                            .where(Video.project_id == project_id)
                            .options(
                                selectinload(Video.transcript).selectinload(Transcript.chunks),
                                selectinload(Video.summary),
                            )
                            .order_by(Video.created_at)
                        )
                        videos = list(videos_result.scalars().all())
                        pending_videos = [v for v in videos if v.status in ("pending", "processing")]

                    await self._update_status(project_id, ProjectStatus.FETCHING, "Fetching transcripts", 1)

                    reuse_videos = [v for v in pending_videos if v.transcript and v.transcript.chunks]
                    download_videos = [v for v in pending_videos if not (v.transcript and v.transcript.chunks)]

                    all_videos_snap = self._build_snapshot(videos)
                    await self._send_update(project_id, {
                        "type": "status_update",
                        "project_id": str(project_id),
                        "data": {
                            "project_id": str(project_id),
                            "stage": "downloading_transcripts",
                            "current_step": 0,
                            "total_steps": max(len(pending_videos), 1),
                            "current_video": None,
                            "errors": [],
                            "video_states": all_videos_snap,
                        },
                    })

                    step = 0
                    failed_errors: list[str] = []

                    for video in reuse_videos:
                        step += 1
                        await self._send_update(project_id, {
                            "type": "status_update",
                            "project_id": str(project_id),
                            "data": {
                                "project_id": str(project_id),
                                "stage": "downloading_transcripts",
                                "current_step": step - 1,
                                "total_steps": max(len(pending_videos), 1),
                                "current_video": video.title or video.youtube_video_id,
                                "errors": [],
                                "video_states": self._build_snapshot(videos),
                            },
                        })

                        try:
                            chunks = list(video.transcript.chunks)

                            if video.summary:
                                await self.session.delete(video.summary)
                                await self.session.flush()

                            summary_result = await self.summarization_service.summarize_chunks(
                                chunks, video.title
                            )

                            video_summary = VideoSummary(
                                video_id=video.id,
                                title=video.title,
                                content=summary_result.summary_text,
                                key_points=_json.dumps(summary_result.key_points),
                                model_used=self.llm_service.model,
                            )
                            self.session.add(video_summary)

                            video.status = "completed"
                            video.processed_at = datetime.now(timezone.utc)
                            video.error_message = None

                        except Exception as e:
                            err_msg = str(e)
                            logger.warning(
                                f"Failed to summarize video {video.youtube_video_id} (transcript reused): {err_msg}"
                            )
                            video.status = "failed"
                            video.error_message = err_msg[:1000]
                            failed_errors.append(f"{video.youtube_video_id}: {err_msg[:200]}")

                        await self.session.flush()

                        # Persist each result before broadcasting it so the HTTP
                        # status endpoint can show completed videos immediately.
                        await self.session.commit()

                        await self._send_update(project_id, {
                            "type": "status_update",
                            "project_id": str(project_id),
                            "data": {
                                "project_id": str(project_id),
                                "stage": "downloading_transcripts",
                                "current_step": step,
                                "total_steps": max(len(pending_videos), 1),
                                "current_video": None,
                                "errors": [],
                                "video_states": self._build_snapshot(videos),
                            },
                        })

                    if download_videos:
                        sem = asyncio.Semaphore(3)

                        async def _fetch_one(video: Video):
                            async with sem:
                                try:
                                    raw_text, segments, language, chapters, metadata = await self.transcript_service.fetch_transcript(
                                        video.youtube_video_id
                                    )
                                    return (video, raw_text, segments, language, metadata, None)
                                except Exception as e:
                                    return (video, None, [], None, {}, str(e))

                        fetch_results = await asyncio.gather(*[_fetch_one(v) for v in download_videos])

                        for video, raw_text, segments, language, metadata, fetch_error in fetch_results:
                            step += 1
                            await self._send_update(project_id, {
                                "type": "status_update",
                                "project_id": str(project_id),
                                "data": {
                                    "project_id": str(project_id),
                                    "stage": "downloading_transcripts",
                                    "current_step": step - 1,
                                    "total_steps": max(len(pending_videos), 1),
                                    "current_video": video.title or video.youtube_video_id,
                                    "errors": [],
                                    "video_states": self._build_snapshot(videos),
                                },
                            })

                            try:
                                if fetch_error:
                                    raise Exception(fetch_error)

                                video.title = metadata.get("title") or video.title
                                video.thumbnail_url = metadata.get("thumbnail_url") or video.thumbnail_url
                                video.duration = metadata.get("duration") or video.duration
                                video.view_count = metadata.get("view_count") or video.view_count
                                await self.session.flush()

                                if not segments:
                                    raise Exception(
                                        f"Could not fetch transcript for {video.youtube_video_id}. "
                                        "No transcript available or could not open transcript panel."
                                    )

                                transcript = Transcript(
                                    video_id=video.id,
                                    content=raw_text,
                                    language=language,
                                )
                                self.session.add(transcript)
                                await self.session.flush()

                                chunks = self.chunking_service.chunk_transcript(segments, chapters=[])

                                for chunk in chunks:
                                    self.session.add(
                                        TranscriptChunk(
                                            transcript_id=transcript.id,
                                            content=chunk.content,
                                            chunk_index=chunk.chunk_index,
                                            start_time=chunk.start_timestamp,
                                            end_time=chunk.end_timestamp,
                                            token_count=chunk.token_count,
                                            chapter_title=chunk.chapter_title,
                                            source_type="transcript",
                                        )
                                    )

                                try:
                                    comment_service = CommentService()
                                    comments = await comment_service.fetch_comments(video.youtube_video_id)
                                    if comments:
                                        for ci, comment_chunk in enumerate(comments):
                                            self.session.add(
                                                TranscriptChunk(
                                                    transcript_id=transcript.id,
                                                    content=comment_chunk,
                                                    chunk_index=len(chunks) + ci,
                                                    token_count=self.llm_service.count_tokens(comment_chunk),
                                                    source_type="comment",
                                                )
                                            )
                                except Exception as e:
                                    logger.warning(f"Comment fetch failed for {video.youtube_video_id}: {e}")

                                await self.session.flush()

                                if video.summary:
                                    await self.session.delete(video.summary)
                                    await self.session.flush()

                                summary_result = await self.summarization_service.summarize_chunks(
                                    chunks, video.title
                                )

                                self.session.add(
                                    VideoSummary(
                                        video_id=video.id,
                                        title=video.title,
                                        content=summary_result.summary_text,
                                        key_points=_json.dumps(summary_result.key_points),
                                        model_used=self.llm_service.model,
                                    )
                                )

                                video.status = "completed"
                                video.processed_at = datetime.now(timezone.utc)
                                video.error_message = None

                            except Exception as e:
                                err_msg = str(e)
                                logger.warning(f"Failed to process video {video.youtube_video_id}: {err_msg}")
                                video.status = "failed"
                                video.error_message = err_msg[:1000]
                                failed_errors.append(f"{video.youtube_video_id}: {err_msg[:200]}")

                            await self.session.flush()

                            # Persist each result before broadcasting it so the HTTP
                            # status endpoint can show completed videos immediately.
                            await self.session.commit()

                            await self._send_update(project_id, {
                                "type": "status_update",
                                "project_id": str(project_id),
                                "data": {
                                    "project_id": str(project_id),
                                    "stage": "downloading_transcripts",
                                    "current_step": step,
                                    "total_steps": max(len(pending_videos), 1),
                                    "current_video": None,
                                    "errors": [],
                                    "video_states": self._build_snapshot(videos),
                                },
                            })

                    await self.session.commit()

                    # New videos can be added while this batch runs. Re-read the project
                    # before finalizing so queued items are not skipped.
                    continue

                video_summaries = self._collect_completed_video_summaries(videos, _json)
                if not video_summaries:
                    raise RuntimeError("All videos failed")

                embedding_error = None
                try:
                    await self._update_status(project_id, ProjectStatus.EMBEDDING, "Generating embeddings", 3)

                    from app.models.orm.transcript import Transcript as TranscriptModel
                    from app.services.contextual_retrieval_service import ContextualRetrievalService

                    ctx_service = ContextualRetrievalService()

                    chunks_result = await self.session.execute(
                        select(TranscriptChunk)
                        .join(TranscriptModel, TranscriptModel.id == TranscriptChunk.transcript_id)
                        .join(Video, Video.id == TranscriptModel.video_id)
                        .options(
                            selectinload(TranscriptChunk.transcript)
                            .selectinload(TranscriptModel.video)
                            .selectinload(Video.summary)
                        )
                        .where(Video.project_id == project_id)
                    )
                    chunks = list(chunks_result.scalars().all())

                    video_summaries_map: dict[uuid.UUID, dict[str, Any]] = {}
                    for chunk in chunks:
                        video = chunk.transcript.video
                        if video.id not in video_summaries_map and video.summary:
                            raw_kp = video.summary.key_points or "[]"
                            try:
                                kp = _json.loads(raw_kp)
                            except Exception:
                                kp = []
                            video_summaries_map[video.id] = {
                                "title": video.title or "",
                                "summary": video.summary.content or "",
                                "key_points": kp,
                            }

                    for chunk in chunks:
                        video = chunk.transcript.video
                        vs = video_summaries_map.get(video.id)
                        if vs:
                            chunk.contextual_content = ctx_service.build_contextual_content(
                                chunk_content=chunk.content,
                                chunk_start_time=float(chunk.start_time) if chunk.start_time else None,
                                video_title=vs["title"],
                                video_summary=vs["summary"],
                                key_points=vs["key_points"],
                            )

                    await self.session.commit()

                    batch_size = 20
                    for i in range(0, len(chunks), batch_size):
                        batch = chunks[i:i + batch_size]
                        items = [
                            (chunk.id, chunk.contextual_content or chunk.content)
                            for chunk in batch
                        ]
                        await self.embedding_service.generate_batch_and_store(
                            items=items,
                            content_type="transcript_chunk",
                        )

                        done = min(i + len(batch), len(chunks))
                        await self._send_update(project_id, {
                            "type": "status_update",
                            "project_id": str(project_id),
                            "data": {
                                "project_id": str(project_id),
                                "stage": "synthesizing",
                                "current_step": done,
                                "total_steps": len(chunks),
                                "current_video": None,
                                "errors": [],
                            },
                        })
                    await self.session.commit()
                except Exception as e:
                    logger.error(f"Embedding generation failed for project {project_id}: {e}")
                    embedding_error = str(e)[:500]
                    await self.session.rollback()

                consolidation_error = None
                try:
                    await self._update_status(project_id, ProjectStatus.COMPLETED, "Finalizing", 4)

                    consolidated_result = await self.summarization_service.consolidate_video_summaries(
                        video_summaries, project.query
                    )

                    existing_cs = await self.session.execute(
                        select(ConsolidatedSummary).where(ConsolidatedSummary.project_id == project_id)
                    )
                    consolidated = existing_cs.scalar_one_or_none()
                    if consolidated:
                        consolidated.summary_text = consolidated_result.summary_text
                        consolidated.key_themes = consolidated_result.key_themes
                        consolidated.consensus_points = consolidated_result.consensus_points
                        consolidated.differing_viewpoints = consolidated_result.differing_viewpoints
                        consolidated.contradictions = consolidated_result.contradictions
                        consolidated.model_used = self.llm_service.model
                    else:
                        consolidated = ConsolidatedSummary(
                            project_id=project_id,
                            summary_text=consolidated_result.summary_text,
                            key_themes=consolidated_result.key_themes,
                            consensus_points=consolidated_result.consensus_points,
                            differing_viewpoints=consolidated_result.differing_viewpoints,
                            contradictions=consolidated_result.contradictions,
                            model_used=self.llm_service.model,
                        )
                        self.session.add(consolidated)
                except Exception as e:
                    logger.error(f"Consolidation failed for project {project_id}: {e}")
                    consolidation_error = str(e)[:500]
                    await self.session.rollback()

                project.status = ProjectStatus.COMPLETED
                if embedding_error or consolidation_error:
                    project.error_message = (
                        f"Embedding: {embedding_error or 'OK'}. "
                        f"Consolidation: {consolidation_error or 'OK'}"
                    )[:1000]

                await self.session.commit()

                refreshed = await self.session.execute(
                    select(Video).where(Video.project_id == project_id)
                )
                if any(v.status in {"pending", "processing"} for v in refreshed.scalars().all()):
                    continue

                await self._send_update(project_id, {"type": "complete"})
                break

        except Exception as e:
            logger.error(f"Error processing project {project_id}: {e}")
            # Only mark as failed if it's a fatal error (e.g. all videos failed).
            # The session may already be rolled back from the inner handlers.
            try:
                await self._update_status(project_id, ProjectStatus.FAILED, f"Error: {str(e)}", 0)
            except Exception:
                pass
            raise

    async def _fetch_videos_metadata(self, videos: list[Video]) -> None:
        """Fetch metadata (title, thumbnail_url, channel_title) for videos using YouTube oEmbed API."""
        import httpx

        async def fetch_one(video: Video):
            url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video.youtube_video_id}&format=json"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(url)
                    if response.status_code == 200:
                        data = response.json()
                        video.title = data.get("title") or video.title
                        if not video.thumbnail_url:
                            video.thumbnail_url = data.get("thumbnail_url")
                        if not video.channel_title:
                            video.channel_title = data.get("author_name")
                        logger.info(f"Fetched oEmbed metadata for video {video.youtube_video_id}: {video.title}")
            except Exception as e:
                logger.warning(f"Failed to fetch oEmbed metadata for {video.youtube_video_id}: {e}")

        # Fetch in parallel
        await asyncio.gather(*[fetch_one(v) for v in videos])

    def _collect_completed_video_summaries(
        self, videos: list[Video], json_module: Any
    ) -> list[dict]:
        summaries: list[dict] = []
        for video in videos:
            if video.status == "completed" and video.summary:
                raw_kp = video.summary.key_points or "[]"
                try:
                    kp = json_module.loads(raw_kp)
                except Exception:
                    kp = []
                summaries.append({
                    "title": video.title,
                    "summary_text": video.summary.content,
                    "key_points": kp,
                    "topics": [],
                })
        return summaries

    @staticmethod
    def _build_snapshot(videos: list[Video]) -> list[dict]:
        queue_positions = {
            str(v.id): idx + 1
            for idx, v in enumerate(sorted(
                (video for video in videos if video.status == "pending"),
                key=lambda item: str(item.created_at or item.updated_at or item.id),
            ))
        }

        return [
            {
                "video_id": str(v.id),
                "youtube_video_id": v.youtube_video_id,
                "title": v.title or v.youtube_video_id,
                "status": v.status,
                "stage": (
                    "queued"
                    if v.status == "pending"
                    else "processing"
                    if v.status == "processing"
                    else "failed"
                    if v.status == "failed"
                    else "ready"
                ),
                "progress": 100 if v.status in {"completed", "failed"} else 50 if v.status == "processing" else 0,
                "queue_position": queue_positions.get(str(v.id)),
            }
            for v in videos
        ]

    async def _update_status(
        self, project_id: uuid.UUID, status: ProjectStatus, message: str, step: int
    ) -> None:
        result = await self.session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if project:
            project.status = status
            await self.session.commit()

    async def _send_update(self, project_id: uuid.UUID, message: dict[str, Any]) -> None:
        await self.ws_manager.send_update(str(project_id), message)
