"""Projects API endpoints."""

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.orm.embedding import Embedding
from app.models.orm.project import Project
from app.models.orm.transcript import Transcript
from app.models.orm.transcript_chunk import TranscriptChunk
from app.models.orm.video import Video
from app.repositories.consolidated_summary_repository import ConsolidatedSummaryRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.video_repository import VideoRepository
from app.repositories.video_summary_repository import VideoSummaryRepository

logger = logging.getLogger(__name__)
router = APIRouter()


class ProjectCreateRequest(BaseModel):
    """Request model for creating a project."""

    name: str = Field(..., min_length=1, max_length=255)
    query: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(None, max_length=2000)
    video_ids: list[str] = Field(..., min_length=1, max_length=20)


class ProjectResponse(BaseModel):
    """Response model for project data."""

    id: str
    name: str
    description: str | None
    query: str
    status: str
    video_count: int
    total_duration: int
    error_message: str | None = None
    created_at: str
    updated_at: str
    video_thumbnails: list[str] = Field(default_factory=list)


class ProjectListResponse(BaseModel):
    """Response model for project list."""

    projects: list[ProjectResponse]
    total: int


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: ProjectCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Create a new project.

    Args:
        request: The project creation request.
        session: Database session.

    Returns:
        The created project.

    Raises:
        HTTPException: If creation fails.
    """
    try:
        project_repo = ProjectRepository(session)
        video_repo = VideoRepository(session)

        # Create project
        project = await project_repo.create(
            name=request.name,
            query=request.query,
            description=request.description,
            status="pending",
            video_count=len(request.video_ids),
        )

        # Add videos
        for video_id in request.video_ids:
            await video_repo.create(
                project_id=project.id,
                youtube_video_id=video_id,
                status="pending",
            )

        # Capture response BEFORE commit to avoid MissingGreenlet on expired attrs
        response = _project_to_response(project)
        project_id = project.id
        await session.commit()

        background_tasks.add_task(_run_orchestrator, project_id)

        return response

    except Exception as e:
        logger.error(f"Error creating project: {e}")
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create project",
        )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    offset: int = 0,
    limit: int = 50,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> ProjectListResponse:
    """List all projects.

    Args:
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status: Optional status filter.
        session: Database session.

    Returns:
        List of projects.
    """
    project_repo = ProjectRepository(session)

    filters: dict[str, str | int] = {}
    if status:
        filters["status"] = status

    projects = await project_repo.list_all(
        offset=offset,
        limit=limit,
        **filters,
    )

    total = await project_repo.count(**filters)

    return ProjectListResponse(
        projects=[_project_to_response(p) for p in projects],
        total=total,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Get a project by ID.

    Args:
        project_id: The project UUID.
        session: Database session.

    Returns:
        The project.

    Raises:
        HTTPException: If project not found.
    """
    project_repo = ProjectRepository(session)

    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    project = await project_repo.get(project_uuid)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return _project_to_response(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a project.

    Args:
        project_id: The project UUID.
        session: Database session.

    Raises:
        HTTPException: If project not found or deletion fails.
    """
    project_repo = ProjectRepository(session)

    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    deleted = await project_repo.delete(project_uuid)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    await session.commit()


class ProjectVideoResponse(BaseModel):
    """Response model for a video inside a project."""

    id: str
    project_id: str
    youtube_video_id: str
    title: str | None
    description: str | None
    channel_title: str | None
    thumbnail_url: str | None
    duration: int | None
    view_count: int | None
    status: str
    error_message: str | None
    downloaded_at: str | None
    processed_at: str | None
    url: str


class ProjectVideoListResponse(BaseModel):
    """Response model for project video list."""

    videos: list[ProjectVideoResponse]
    total: int


class ProcessingStatusResponse(BaseModel):
    """Response model for project processing status."""

    project_id: str
    stage: str
    current_step: int
    total_steps: int
    current_video: str | None
    errors: list[str]
    queued_count: int = 0
    processing_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    overall_progress: float = 0.0
    video_states: list[dict] = Field(default_factory=list)
    embedding_count: int = 0
    embedding_total: int = 0
    embedding_error: str | None = None


class ConsolidatedSynthesisResponse(BaseModel):
    """Response model for consolidated synthesis."""

    id: str
    project_id: str
    title: str
    main_takeaways: list[str]
    key_concepts: list[str]
    speaker_perspective: str
    notable_quotes: list[dict]
    created_at: str


class IndividualSummaryResponse(BaseModel):
    """Response model for individual video summary."""

    id: str
    project_id: str
    video_id: str
    title: str
    url: str
    thumbnail_url: str
    summary: str
    key_points: list[str]
    created_at: str


@router.get("/{project_id}/videos", response_model=ProjectVideoListResponse)
async def get_project_videos(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> ProjectVideoListResponse:
    """List all videos for a project."""
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    video_repo = VideoRepository(session)
    videos = await video_repo.list_by_project(project_uuid)

    return ProjectVideoListResponse(
        videos=[_video_to_response(v) for v in videos],
        total=len(videos),
    )


class AddVideoRequest(BaseModel):
    youtube_video_id: str = Field(..., min_length=1, max_length=20)


@router.post("/{project_id}/videos", response_model=ProjectVideoResponse, status_code=status.HTTP_201_CREATED)
async def add_video_to_project(
    project_id: str,
    request: AddVideoRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ProjectVideoResponse:
    """Add a video to an existing project and process it."""
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid project ID format")

    project_repo = ProjectRepository(session)
    project = await project_repo.get(project_uuid)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    video_repo = VideoRepository(session)
    existing = await video_repo.get_by_youtube_id(request.youtube_video_id)
    if existing and str(existing.project_id) == project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Video already in this project")

    video = await video_repo.create(
        project_id=project_uuid,
        youtube_video_id=request.youtube_video_id,
        status="pending",
    )
    project.status = "processing"
    project.video_count += 1
    # Capture response BEFORE commit to avoid MissingGreenlet on expired attrs
    response = _video_to_response(video)
    await session.commit()

    background_tasks.add_task(_run_orchestrator, project_uuid)

    return response


@router.post("/{project_id}/videos/{video_id}/retry", response_model=ProjectVideoResponse)
async def retry_video(
    project_id: str,
    video_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ProjectVideoResponse:
    """Reset a failed video to pending and reprocess it."""
    try:
        project_uuid = uuid.UUID(project_id)
        video_uuid = uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    video_repo = VideoRepository(session)
    project_repo = ProjectRepository(session)

    video = await video_repo.get(video_uuid)
    if not video or str(video.project_id) != str(project_uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found in project")

    project = await project_repo.get(project_uuid)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    video.status = "pending"
    video.error_message = None
    project.status = "processing"

    # Capture response BEFORE commit to avoid MissingGreenlet on expired attrs
    response = _video_to_response(video)
    await session.commit()
    background_tasks.add_task(_run_orchestrator, project_uuid)

    return response


@router.post("/{project_id}/retry", response_model=ProjectResponse)
async def retry_project(
    project_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Reset failed videos and reprocess the project."""
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    project_repo = ProjectRepository(session)
    project = await project_repo.get(project_uuid)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Reset only failed videos. Completed videos stay intact.
    video_repo = VideoRepository(session)
    all_videos = await video_repo.list_by_project(project_uuid)
    for v in all_videos:
        if v.status == "failed":
            v.status = "pending"
            v.error_message = None
    project.status = "processing"
    project.error_message = None

    # Capture response BEFORE commit — commit expires ORM attributes,
    # and lazy-loading them back in an async context hits MissingGreenlet.
    response = _project_to_response(project)
    await session.commit()

    background_tasks.add_task(_run_orchestrator, project_uuid)
    return response


@router.delete("/{project_id}/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_video_from_project(
    project_id: str,
    video_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a video from a project."""
    try:
        project_uuid = uuid.UUID(project_id)
        video_uuid = uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    video_repo = VideoRepository(session)
    video = await video_repo.get(video_uuid)
    if not video or str(video.project_id) != str(project_uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found in project")

    await video_repo.delete(video_uuid)
    project_repo = ProjectRepository(session)
    project = await project_repo.get(project_uuid)
    if project and project.video_count > 0:
        project.video_count -= 1
    await session.commit()


@router.get("/{project_id}/status", response_model=ProcessingStatusResponse)
async def get_project_status(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> ProcessingStatusResponse:
    """Get processing status for a project."""
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    project_repo = ProjectRepository(session)
    project = await project_repo.get(project_uuid)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    video_repo = VideoRepository(session)
    videos = await video_repo.list_by_project(project_uuid)

    completed_count = sum(1 for v in videos if v.status == "completed")
    processing_count = sum(1 for v in videos if v.status == "processing")
    queued_count = sum(1 for v in videos if v.status == "pending")
    failed_count = sum(1 for v in videos if v.status == "failed")

    embedding_count = 0
    embedding_total = 0
    try:
        embedding_result = await session.execute(
            select(
                func.count(func.distinct(TranscriptChunk.id)),
                func.count(func.distinct(Embedding.chunk_id)),
            )
            .join(Transcript, Transcript.id == TranscriptChunk.transcript_id)
            .join(Video, Video.id == Transcript.video_id)
            .outerjoin(Embedding, Embedding.chunk_id == TranscriptChunk.id)
            .where(Video.project_id == project_uuid)
        )
        embedding_row = embedding_result.one()
        embedding_total = embedding_row[0] if isinstance(embedding_row[0], int) else 0
        embedding_count = embedding_row[1] if isinstance(embedding_row[1], int) else 0
    except Exception as e:
        logger.warning("Could not inspect embedding progress for project %s: %s", project_id, e)

    embedding_error = None
    if project.error_message:
        embedding_message = project.error_message.split(" Consolidation:", 1)[0].strip()
        if embedding_message and embedding_message != "Embedding: OK.":
            embedding_error = embedding_message.removeprefix("Embedding:").strip()

    if project.status == "completed" and embedding_total > embedding_count:
        embedding_error = (
            f"Embedding index is incomplete ({embedding_count}/{embedding_total} transcript chunks)."
        )

    # Auto-correct project status based on actual video states.
    # Handles: stuck processing, orchestrator crash after videos done, etc.
    if not _is_orchestrator_alive(project_id):
        if project.status in ("processing", "fetching", "embedding"):
            if completed_count > 0:
                project.status = "completed"
            elif failed_count > 0:
                project.status = "failed"
            else:
                project.status = "failed"
            await session.commit()
        elif project.status == "failed" and completed_count > 0 and failed_count == 0:
            # Orchestrator crashed after all videos completed (e.g. embedding failed)
            project.status = "completed"
            await session.commit()

    total = len(videos)
    finished_count = completed_count + failed_count
    failed = [v for v in videos if v.status == "failed"]
    active_videos = [v for v in videos if v.status == "processing"]

    stage_map = {
        "pending": "initializing",
        "processing": "downloading_transcripts",
        "fetching": "downloading_transcripts",
        "embedding": "synthesizing",
        "completed": "complete",
        "failed": "failed",
    }
    stage = stage_map.get(project.status, "initializing")

    ordered_videos = sorted(
        videos,
        key=lambda v: str(v.created_at or v.updated_at or v.id),
    )
    queue_positions = {
        str(v.id): idx + 1
        for idx, v in enumerate(v for v in ordered_videos if v.status == "pending")
    }

    video_states = [
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
            "progress": 100 if v.status in ("completed", "failed") else 50 if v.status == "processing" else 0,
            "queue_position": queue_positions.get(str(v.id)),
        }
        for v in videos
    ]

    current_video = (
        active_videos[0].title or active_videos[0].youtube_video_id
        if active_videos
        else None
    )
    if total == 0:
        overall_progress = 100.0
    elif project.status == "completed" or finished_count == total:
        overall_progress = 100.0
    else:
        overall_progress = min(99.0, round((finished_count / total) * 100, 1))

    return ProcessingStatusResponse(
        project_id=project_id,
        stage=stage,
        current_step=finished_count,
        total_steps=max(total, 1),
        current_video=current_video,
        errors=[f"{v.youtube_video_id}: {v.error_message}" for v in failed if v.error_message],
        queued_count=queued_count,
        processing_count=processing_count,
        completed_count=completed_count,
        failed_count=failed_count,
        overall_progress=overall_progress,
        video_states=video_states,
        embedding_count=embedding_count,
        embedding_total=embedding_total,
        embedding_error=embedding_error,
    )


@router.get("/{project_id}/synthesis/consolidated", response_model=ConsolidatedSynthesisResponse)
async def get_consolidated_synthesis(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> ConsolidatedSynthesisResponse:
    """Get the consolidated synthesis for a project."""
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    summary_repo = ConsolidatedSummaryRepository(session)
    summary = await summary_repo.get_by_field("project_id", project_uuid)

    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Synthesis not found",
        )

    return ConsolidatedSynthesisResponse(
        id=str(summary.id),
        project_id=str(summary.project_id),
        title=summary.summary_text[:80] if summary.summary_text else "Synthesis",
        main_takeaways=[
            item["theme"] if isinstance(item, dict) else str(item)
            for item in (summary.key_themes or [])
        ],
        key_concepts=[
            item["point"] if isinstance(item, dict) else str(item)
            for item in (summary.consensus_points or [])
        ],
        speaker_perspective=summary.summary_text or "",
        notable_quotes=[],
        created_at=summary.created_at.isoformat(),
    )


@router.get("/{project_id}/synthesis/summaries", response_model=list[IndividualSummaryResponse])
async def get_video_summaries(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[IndividualSummaryResponse]:
    """Get per-video summaries for a project."""
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    video_repo = VideoRepository(session)
    videos = await video_repo.list_by_project(project_uuid)

    results = []
    for video in videos:
        if not video.summary:
            continue
        key_points: list[str] = []
        if video.summary.key_points:
            try:
                raw = json.loads(video.summary.key_points)
                key_points = [
                    item["point"] if isinstance(item, dict) else str(item)
                    for item in raw
                ]
            except (json.JSONDecodeError, TypeError, KeyError):
                key_points = []
        results.append(
            IndividualSummaryResponse(
                id=str(video.summary.id),
                project_id=project_id,
                video_id=str(video.id),
                title=video.summary.title or video.title or video.youtube_video_id,
                url=f"https://www.youtube.com/watch?v={video.youtube_video_id}",
                thumbnail_url=video.thumbnail_url or "",
                summary=video.summary.content,
                key_points=key_points,
                created_at=video.summary.created_at.isoformat(),
            )
        )

    return results


def _video_to_response(video: Any) -> ProjectVideoResponse:
    """Convert Video ORM model to ProjectVideoResponse."""
    return ProjectVideoResponse(
        id=str(video.id),
        project_id=str(video.project_id),
        youtube_video_id=video.youtube_video_id,
        title=video.title,
        description=getattr(video, "description", None),
        channel_title=getattr(video, "channel_title", None),
        thumbnail_url=video.thumbnail_url,
        duration=video.duration,
        view_count=video.view_count,
        status=video.status,
        error_message=getattr(video, "error_message", None),
        downloaded_at=video.downloaded_at.isoformat() if video.downloaded_at else None,
        processed_at=video.processed_at.isoformat() if video.processed_at else None,
        url=f"https://www.youtube.com/watch?v={video.youtube_video_id}",
    )


# In-memory set of projects with a live orchestrator task.
# Cleared on backend restart — this is intentional: we use it to detect
# projects that are "processing" in the DB but have no running task.
_active_orchestrators: set[str] = set()


def _is_orchestrator_alive(project_id: uuid.UUID | str) -> bool:
    return str(project_id) in _active_orchestrators


async def _run_orchestrator(project_id: uuid.UUID) -> None:
    """Run the processing orchestrator for a project in the background.

    Only one orchestrator runs per project at a time. If one is already
    active, this call is a no-op — the running orchestrator will pick up
    any newly-added pending videos.
    """
    from app.core.db import async_session_maker as AsyncSessionLocal, engine
    from app.core.websocket import manager
    from app.services.orchestrator_service import ProcessingOrchestrator

    pid = str(project_id)
    lock_sql = text("SELECT pg_try_advisory_lock(hashtextextended(:project_key, 0))")
    unlock_sql = text("SELECT pg_advisory_unlock(hashtextextended(:project_key, 0))")

    async with engine.connect() as conn:
        acquired = (await conn.execute(lock_sql, {"project_key": pid})).scalar()
        if not acquired:
            logger.info(f"Orchestrator already running for project {pid}, skipping")
            return

        _active_orchestrators.add(pid)
        try:
            async with AsyncSessionLocal() as session:
                try:
                    orchestrator = ProcessingOrchestrator(session, manager)
                    await orchestrator.process_project(project_id)
                except Exception as e:
                    logger.error(f"Orchestrator failed for project {project_id}: {e}")
        finally:
            _active_orchestrators.discard(pid)
            await conn.execute(unlock_sql, {"project_key": pid})


def _project_to_response(project: Project) -> ProjectResponse:
    """Convert Project model to response.

    Args:
        project: The Project model.

    Returns:
        ProjectResponse instance.
    """
    video_thumbnails = []
    if project.videos:
        for v in project.videos:
            url = v.thumbnail_url or f"https://img.youtube.com/vi/{v.youtube_video_id}/mqdefault.jpg"
            video_thumbnails.append(url)

    return ProjectResponse(
        id=str(project.id),
        name=project.name,
        description=project.description,
        query=project.query,
        status=project.status,
        video_count=project.video_count,
        total_duration=project.total_duration,
        error_message=getattr(project, "error_message", None),
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
        video_thumbnails=video_thumbnails,
    )
