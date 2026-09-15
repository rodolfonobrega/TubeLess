'use client'

import { useCallback, useRef, useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ArrowLeft, RefreshCw, Trash2, AlertTriangle, X, FileText, Video, MessageSquare, BookOpen, Calendar } from 'lucide-react'
import Link from 'next/link'
import { AppHeader } from '@/components/AppHeader'
import { ProcessingPipeline } from '@/components/ProcessingPipeline'
import { ConsolidatedView } from '@/components/ConsolidatedView'
import { VideoSummaryList } from '@/components/VideoSummaryCard'
import { ChatContainer } from '@/components/ChatContainer'
import { VideoManager } from '@/components/VideoManager'
import { useProject, useSynthesis, useSummaries, useChatMessages, useDeleteProject } from '@/hooks/use-project'
import { useWebSocket } from '@/hooks/use-websocket'
import { useProjectStore } from '@/stores/project-store'
import { projectsApi } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'

const SIDEBAR_TABS = [
  { value: 'videos', label: 'Videos', icon: Video },
  { value: 'chat', label: 'Chat', icon: MessageSquare },
  { value: 'synthesis', label: 'Synthesis', icon: FileText },
  { value: 'summaries', label: 'Summaries', icon: BookOpen },
] as const

type TabValue = typeof SIDEBAR_TABS[number]['value']

export default function ProjectPage() {
  const params = useParams()
  const router = useRouter()
  const projectId = params.id as string
  const queryClient = useQueryClient()

  const activeTab = useProjectStore((s) => s.activeTab) as TabValue
  const setActiveTab = useProjectStore((s) => s.setActiveTab)
  const setProcessingStatus = useProjectStore((s) => s.setProcessingStatus)
  const { project, videos, status, isLoading, isError } = useProject(projectId)

  const isProcessing = ['pending', 'processing', 'fetching', 'embedding'].includes(project?.status ?? '')
  const isFailed = project?.status === 'failed'
  const isCompleted = project?.status === 'completed'
  const isDone = isCompleted || isFailed

  const [retrying, setRetrying] = useState(false)
  const [errorBannerDismissed, setErrorBannerDismissed] = useState(false)

  const { synthesis, isLoading: synthesisLoading } = useSynthesis(isDone ? projectId : null)
  const { summaries, isLoading: summariesLoading } = useSummaries(isDone ? projectId : null)
  const { messages, sendMessage, isSending } = useChatMessages(projectId)
  const deleteProject = useDeleteProject()

  const onStatusUpdate = useCallback((data: Parameters<typeof setProcessingStatus>[0]) => {
    setProcessingStatus(data)
  }, [setProcessingStatus])

  const onComplete = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    queryClient.invalidateQueries({ queryKey: ['project-videos', projectId] })
  }, [queryClient, projectId])

  const onError = useCallback((message: string) => {
    console.error('Processing error:', message)
  }, [])

  useWebSocket(isProcessing ? projectId : null, { onStatusUpdate, onComplete, onError })

  const prevProcessingRef = useRef(isProcessing)
  useEffect(() => {
    if (prevProcessingRef.current && isDone) {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project-videos', projectId] })
    }
    prevProcessingRef.current = isProcessing
  }, [isProcessing, isDone, queryClient, projectId])

  useEffect(() => {
    setErrorBannerDismissed(false)
    setRetrying(false)
  }, [project?.status])

  const handleDelete = async () => {
    if (confirm('Are you sure you want to delete this project?')) {
      try {
        await deleteProject.mutateAsync(projectId)
        router.push('/projects')
      } catch (err) {
        alert(err instanceof Error ? err.message : 'Failed to delete project')
      }
    }
  }

  const handleRetry = async () => {
    setRetrying(true)
    try {
      await projectsApi.retryAll(projectId)
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project-videos', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project-status', projectId] })
      setErrorBannerDismissed(false)
      setProcessingStatus(null)
    } catch {
      setRetrying(false)
    } finally {
      setRetrying(false)
    }
  }

  if (isError) {
    return (
      <div className="min-h-screen bg-white">
        <AppHeader />
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center max-w-sm">
            <h2 className="text-xl font-semibold text-gray-900 mb-2">Project Not Found</h2>
            <p className="text-gray-500 text-sm mb-6">
              The project you're looking for doesn't exist or you don't have access to it.
            </p>
            <Link
              href="/projects"
              className="inline-flex items-center gap-2 bg-gray-900 text-white text-sm font-medium px-5 py-2.5 rounded-md hover:bg-gray-700 transition-colors"
            >
              Go Back to Projects
            </Link>
          </div>
        </div>
      </div>
    )
  }

  const failedVideos = (videos || []).filter((video) => video.status === 'failed')
  const completedVideos = (videos || []).filter((video) => video.status === 'completed')
  const hasFailedVideos = failedVideos.length > 0
  const embeddingCount = status?.embedding_count ?? 0
  const embeddingTotal = status?.embedding_total ?? 0
  const hasEmbeddingErrorMessage = Boolean(
    project?.error_message &&
    project.error_message.startsWith('Embedding:') &&
    !project.error_message.startsWith('Embedding: OK.')
  )
  const hasEmbeddingIssue = Boolean(status?.embedding_error) ||
    hasEmbeddingErrorMessage ||
    (isCompleted && embeddingTotal > 0 && embeddingCount < embeddingTotal)
  const hasProcessingIssue = hasEmbeddingIssue || Boolean(project?.error_message)
  const canRetry = hasFailedVideos || hasProcessingIssue
  const retryActionLabel = hasEmbeddingIssue && hasFailedVideos
    ? 'Retry processing'
    : hasEmbeddingIssue
      ? 'Retry embeddings'
      : hasFailedVideos
        ? 'Retry failed'
        : 'Retry processing'
  const issueTitle = hasEmbeddingIssue
    ? 'Embedding index incomplete'
    : hasFailedVideos
      ? 'Some videos failed'
      : 'Processing finished with warnings'
  const issueDescription = hasEmbeddingIssue
    ? `The search index contains ${embeddingCount} of ${embeddingTotal || 'the available'} transcript chunks. Regenerate it to restore transcript search and citations.`
    : hasFailedVideos
      ? completedVideos.length > 0
        ? `${failedVideos.length} video${failedVideos.length !== 1 ? 's' : ''} failed, ${completedVideos.length} completed. You can review the completed content or retry the failures.`
        : `All ${failedVideos.length} videos failed to process. Check the errors and try again.`
      : 'The project finished, but one processing stage reported an error. You can retry the processing pipeline.'
  const technicalError = project?.error_message || status?.embedding_error

  // Project initials / avatar
  const projectInitials = project?.name
    ? project.name.split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase()
    : '?'

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <AppHeader />

      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <div className="text-center">
            <div className="mx-auto mb-3 h-10 w-10 animate-spin rounded-full border-[3px] border-gray-900 border-t-transparent" />
            <p className="text-sm text-gray-500">Loading project...</p>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          <aside className="flex w-[280px] flex-shrink-0 flex-col overflow-y-auto border-r border-gray-200 bg-white">
            <div className="mb-4 px-5 pt-5">
              <Link
                href="/projects"
                className="inline-flex items-center gap-1.5 text-sm text-gray-500 transition-colors hover:text-gray-800"
              >
                <ArrowLeft className="h-4 w-4" />
                Back to Projects
              </Link>
            </div>

            <div className="px-5 mb-5">
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-gray-100 text-base font-bold text-gray-600">
                {projectInitials}
              </div>
              <h1 className="mb-1 text-base font-semibold leading-snug text-gray-900">
                {project?.name}
              </h1>
              {project?.description && (
                <p className="mb-2 line-clamp-2 text-sm text-gray-500">{project.description}</p>
              )}
              <div className="flex items-center gap-1.5 text-xs text-gray-400">
                <Calendar className="h-3 w-3" />
                <span>Created {project?.created_at ? formatDate(project.created_at) : '—'}</span>
              </div>
            </div>

            <nav className="mb-4 px-3">
              {SIDEBAR_TABS.map(({ value, label, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setActiveTab(value)}
                  className={cn(
                    'mb-0.5 flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                    activeTab === value
                      ? 'bg-gray-100 text-gray-900'
                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-800'
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className="h-4 w-4" />
                    {label}
                  </div>
                  {value === 'videos' && (
                    <span className="rounded-full bg-gray-200 px-1.5 py-0.5 text-xs text-gray-600">
                      {(videos || []).length}
                    </span>
                  )}
                </button>
              ))}
            </nav>

            <div className="mt-auto flex items-center gap-2 border-t border-gray-100 px-5 pb-5 pt-4">
              <button
                type="button"
                onClick={handleRetry}
                disabled={retrying || !canRetry}
                title={retryActionLabel}
                className="flex-1 rounded-lg border border-gray-200 py-2 text-xs text-gray-500 transition-colors hover:text-gray-800 disabled:opacity-40"
              >
                <RefreshCw className={cn('mr-1 inline h-3 w-3', retrying && 'animate-spin')} />
                {retryActionLabel}
              </button>
              <button
                type="button"
                onClick={handleDelete}
                title="Delete project"
                className="flex-1 rounded-lg border border-gray-200 py-2 text-xs text-red-500 transition-colors hover:border-red-200 hover:text-red-700"
              >
                <Trash2 className="mr-1 inline h-3 w-3" />
                Delete
              </button>
            </div>
          </aside>

          <main className="flex-1 overflow-auto">
            {isProcessing && (
              <div className="px-6 pt-5">
                <ProcessingPipeline
                  currentStage={status?.stage || 'initializing'}
                  currentStep={status?.current_step || 0}
                  totalSteps={status?.total_steps || Math.max((videos || []).length, 1)}
                  currentVideo={status?.current_video || null}
                  errors={status?.errors || []}
                  videoStates={status?.video_states || []}
                  queuedCount={status?.queued_count || 0}
                  processingCount={status?.processing_count || 0}
                  completedCount={status?.completed_count || 0}
                  failedCount={status?.failed_count || 0}
                  overallProgress={status?.overall_progress || 0}
                />
              </div>
            )}

            {(hasFailedVideos || hasProcessingIssue) && !errorBannerDismissed && (
              <div className={cn(
                'mx-6 mt-5 rounded-xl p-4',
                hasFailedVideos ? 'border border-red-200 bg-red-50' : 'border border-amber-200 bg-amber-50'
              )}>
                <div className="flex items-start gap-3">
                  <AlertTriangle className={cn(
                    'mt-0.5 h-4 w-4 flex-shrink-0',
                    hasFailedVideos ? 'text-red-500' : 'text-amber-500'
                  )} />
                  <div className="min-w-0 flex-1">
                    <h3 className={cn(
                      'text-sm font-semibold',
                      hasFailedVideos ? 'text-red-700' : 'text-amber-800'
                    )}>{issueTitle}</h3>
                    <p className={cn(
                      'mt-0.5 text-sm',
                      hasFailedVideos ? 'text-red-600/80' : 'text-amber-700/90'
                    )}>
                      {issueDescription}
                    </p>
                    {technicalError && (
                      <details className="mt-2">
                        <summary className={cn(
                          'cursor-pointer text-xs',
                          hasFailedVideos ? 'text-red-500' : 'text-amber-700'
                        )}>Technical details</summary>
                        <pre className={cn(
                          'mt-1 whitespace-pre-wrap break-all rounded p-2 text-xs',
                          hasFailedVideos ? 'bg-red-100 text-red-400' : 'bg-amber-100 text-amber-700'
                        )}>
                          {technicalError}
                        </pre>
                      </details>
                    )}
                    <div className="mt-3">
                      <button
                        type="button"
                        onClick={handleRetry}
                        disabled={retrying}
                        className={cn(
                          'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50',
                          hasFailedVideos
                            ? 'border-red-300 text-red-700 hover:bg-red-100'
                            : 'border-amber-300 text-amber-800 hover:bg-amber-100'
                        )}
                      >
                        {retrying ? (
                          <>
                            <div className="h-3 w-3 animate-spin rounded-full border-2 border-red-700 border-t-transparent" />
                            Retrying...
                          </>
                        ) : (
                          <>
                            <RefreshCw className="h-3 w-3" />
                            {retryActionLabel}
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setErrorBannerDismissed(true)}
                    className={cn(
                      'flex-shrink-0',
                      hasFailedVideos ? 'text-red-400 hover:text-red-600' : 'text-amber-500 hover:text-amber-700'
                    )}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )}

            <motion.div
              key={activeTab}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.15 }}
              className={cn(activeTab === 'chat' ? 'h-[calc(100vh-57px)]' : 'p-6', 'flex flex-col')}
            >
              {activeTab === 'videos' && (
                <>
                  <VideoManager projectId={projectId} videos={videos || []} />
                </>
              )}

              {activeTab === 'synthesis' && synthesis && (
                <>
                  <div className="mb-5 flex items-center justify-between">
                    <div>
                      <h2 className="text-xl font-bold text-gray-900">Cross-Video Synthesis</h2>
                      <p className="text-sm text-gray-500">
                        Generated {project?.updated_at ? formatDate(project.updated_at) : ''} · {(videos || []).length} sources
                      </p>
                    </div>
                  </div>
                  <ConsolidatedView synthesis={synthesis} />
                </>
              )}
              {activeTab === 'synthesis' && synthesisLoading && (
                <div className="flex h-64 items-center justify-center">
                  <div className="h-8 w-8 animate-spin rounded-full border-[3px] border-gray-900 border-t-transparent" />
                </div>
              )}
              {activeTab === 'synthesis' && !isDone && !synthesisLoading && (
                <div className="flex h-64 items-center justify-center text-gray-400">
                  <div className="text-center">
                    <FileText className="mx-auto mb-3 h-12 w-12 opacity-20" />
                    <p className="text-sm">Synthesis will appear when processing finishes.</p>
                  </div>
                </div>
              )}
              {activeTab === 'synthesis' && isDone && !synthesis && !synthesisLoading && (
                <div className="flex h-64 items-center justify-center text-gray-400">
                  <div className="text-center">
                    <FileText className="mx-auto mb-3 h-12 w-12 opacity-20" />
                    <p className="text-sm">No synthesis available yet.</p>
                    <p className="mt-1 text-xs text-gray-400">Synthesis is generated after all videos are processed.</p>
                  </div>
                </div>
              )}

              {activeTab === 'summaries' && (
                <>
                  <div className="mb-5">
                    <h2 className="text-xl font-bold text-gray-900">Video Summaries</h2>
                    <p className="text-sm text-gray-500">Individual AI-generated summaries for each video</p>
                  </div>
                  {isDone ? (
                    <VideoSummaryList summaries={summaries || []} isLoading={summariesLoading} />
                  ) : (
                    <div className="flex h-64 items-center justify-center text-gray-400">
                      <div className="text-center">
                        <BookOpen className="mx-auto mb-3 h-12 w-12 opacity-20" />
                        <p className="text-sm">Summaries will appear when processing finishes.</p>
                      </div>
                    </div>
                  )}
                </>
              )}

              {activeTab === 'chat' && (
                <ChatContainer
                  messages={messages}
                  videos={videos || []}
                  onSendMessage={(msg, videoIds) => sendMessage(msg, videoIds)}
                  isLoading={isSending}
                />
              )}
            </motion.div>
          </main>
        </div>
      )}
    </div>
  )
}
