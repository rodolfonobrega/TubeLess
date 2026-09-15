// Core domain models matching backend

export interface Video {
  id: string
  project_id: string
  youtube_video_id: string
  title: string | null
  description: string | null
  channel_title: string | null
  thumbnail_url: string | null
  duration: number | null
  view_count: number | null
  status: string
  error_message?: string | null
  downloaded_at: string | null
  processed_at: string | null
  url: string // computed: https://youtube.com/watch?v={youtube_video_id}
}

export interface VideoSource {
  video_id: string
  title: string
  url: string
  thumbnail_url: string
  channel: string
  duration_seconds: number
}

export interface ConsolidatedSynthesis {
  id: string
  project_id: string
  title: string
  main_takeaways: string[]
  key_concepts: string[]
  speaker_perspective: string
  notable_quotes: Quote[]
  created_at: string
}

export interface Quote {
  text: string
  timestamp: number
  video_id: string
  video_title: string
  context: string
}

export interface IndividualSummary {
  id: string
  project_id: string
  video_id: string
  title: string
  url: string
  thumbnail_url: string
  summary: string
  key_points: string[]
  created_at: string
}

export interface ConsolidatedInsight {
  id: string
  synthesis_id: string
  theme: string
  description: string
  video_sources: VideoSource[]
  relevant_timestamps: TimestampReference[]
}

export interface TimestampReference {
  video_id: string
  seconds: number
  title: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources: ChatSource[]
  project_id?: string
  created_at?: string
}

export interface ChatSource {
  video_id: string
  video_title: string
  youtube_url: string
  timestamp: number
  snippet: string
  source_type: string
  similarity: number
}

export interface Project {
  id: string
  name: string
  description: string | null
  query: string
  status: 'pending' | 'processing' | 'fetching' | 'embedding' | 'completed' | 'failed'
  video_count: number
  total_duration: number
  error_message?: string | null
  created_at: string
  updated_at: string
  video_thumbnails?: string[]
}

export interface VideoState {
  video_id: string
  youtube_video_id: string
  title: string
  status: string
  stage?: string
  progress?: number
  queue_position?: number | null
}

export interface ProcessingStatus {
  project_id: string
  stage: ProcessingStage
  current_step: number
  total_steps: number
  current_video: string | null
  errors: string[]
  queued_count: number
  processing_count: number
  completed_count: number
  failed_count: number
  overall_progress: number
  video_states: VideoState[]
  embedding_count: number
  embedding_total: number
  embedding_error?: string | null
}

export type ProcessingStage =
  | 'initializing'
  | 'fetching_metadata'
  | 'downloading_transcripts'
  | 'generating_summaries'
  | 'synthesizing'
  | 'complete'
  | 'failed'

export interface CreateProjectRequest {
  name: string
  query: string
  description?: string
  video_ids: string[]
}

export interface ChatRequest {
  message: string
  project_id: string
  video_ids?: string[]
}

export interface ChatResponse {
  id: string
  role: 'assistant'
  content: string
  sources: ChatSource[]
  created_at: string
}

// API Response wrappers (kept for type compatibility)
export interface ApiResponse<T> {
  data: T
  message?: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// WebSocket message types
export interface WebSocketMessage {
  type: 'status_update' | 'progress' | 'complete' | 'error'
  project_id: string
  data: unknown
}

export interface StatusUpdateMessage extends WebSocketMessage {
  type: 'status_update'
  data: ProcessingStatus
}

export interface ProgressMessage extends WebSocketMessage {
  type: 'progress'
  data: {
    current_step: number
    total_steps: number
    message: string
  }
}

export interface CompleteMessage extends WebSocketMessage {
  type: 'complete'
  data: {
    project_id: string
  }
}

export interface ErrorMessage extends WebSocketMessage {
  type: 'error'
  data: {
    message: string
  }
}
