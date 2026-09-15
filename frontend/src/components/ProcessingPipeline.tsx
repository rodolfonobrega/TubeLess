'use client'

import { motion } from 'framer-motion'
import { AlertCircle, Check, Loader2, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Progress } from '@/components/ui/progress'
import type { ProcessingStage, VideoState } from '@/types'

interface ProcessingPipelineProps {
  currentStage: ProcessingStage
  currentStep: number
  totalSteps: number
  currentVideo: string | null
  errors: string[]
  videoStates?: VideoState[]
  queuedCount?: number
  processingCount?: number
  completedCount?: number
  failedCount?: number
  overallProgress?: number
  className?: string
}

const stages: { key: ProcessingStage; label: string; description: string }[] = [
  { key: 'initializing', label: 'Queued', description: 'Waiting for the pipeline to start.' },
  { key: 'downloading_transcripts', label: 'Processing videos', description: 'Downloading captions, reading transcripts, and generating summaries.' },
  { key: 'generating_summaries', label: 'Summarizing', description: 'Generating per-video summaries in the background.' },
  { key: 'synthesizing', label: 'Embedding + synthesis', description: 'Building embeddings and the consolidated synthesis.' },
]

export function ProcessingPipeline({
  currentStage,
  currentStep,
  totalSteps,
  currentVideo,
  errors,
  videoStates = [],
  queuedCount = 0,
  processingCount = 0,
  completedCount = 0,
  failedCount = 0,
  overallProgress,
  className,
}: ProcessingPipelineProps) {
  const stageKeys = stages.map((stage) => stage.key)
  const currentStageIndex = stageKeys.indexOf(currentStage)
  const progressValue =
    typeof overallProgress === 'number'
      ? Math.max(0, Math.min(100, overallProgress))
      : totalSteps > 0
        ? Math.max(0, Math.min(100, (currentStep / totalSteps) * 100))
        : 0
  const activeVideos = videoStates.filter((video) => video.status === 'processing')
  const queuedVideos = videoStates.filter((video) => video.status === 'pending')
  const completedVideos = videoStates.filter((video) => video.status === 'completed')
  const headlineLabel =
    currentStage === 'complete'
      ? 'Complete'
      : currentStage === 'failed'
        ? 'Attention'
        : stages[currentStageIndex]?.label || 'Processing'
  const headlineDescription =
    currentStage === 'complete'
      ? 'The project finished processing.'
      : currentStage === 'failed'
        ? 'The pipeline stopped before finishing.'
        : stages[currentStageIndex]?.description || 'The project is being processed.'

  return (
    <div className={cn('space-y-4 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm', className)}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700">
              {currentStage === 'failed' ? 'Attention' : currentStage === 'complete' ? 'Complete' : 'Processing'}
            </span>
            {currentVideo && (
              <span className="text-xs text-gray-500 truncate">
                Active: {currentVideo}
              </span>
            )}
          </div>
          <h3 className="mt-2 text-sm font-semibold text-gray-900">{headlineLabel}</h3>
          <p className="mt-1 text-sm text-gray-500">{headlineDescription}</p>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 lg:min-w-[360px]">
          <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2">
            <div className="text-gray-400">Done</div>
            <div className="mt-1 font-semibold text-gray-900">{completedCount}</div>
          </div>
          <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2">
            <div className="text-gray-400">Queued</div>
            <div className="mt-1 font-semibold text-gray-900">{queuedCount || queuedVideos.length}</div>
          </div>
          <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2">
            <div className="text-gray-400">Active</div>
            <div className="mt-1 font-semibold text-gray-900">{processingCount || activeVideos.length}</div>
          </div>
          <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2">
            <div className="text-gray-400">Failed</div>
            <div className="mt-1 font-semibold text-gray-900">{failedCount}</div>
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>{currentStep} / {Math.max(totalSteps, 1)} finished</span>
          <span>{Math.round(progressValue)}%</span>
        </div>
        <Progress value={progressValue} className="h-2 bg-gray-100" />
      </div>

      {activeVideos.length > 0 && (
        <div className="space-y-2 rounded-xl border border-indigo-100 bg-indigo-50/60 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-indigo-700">
            Active videos
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {activeVideos.map((video) => (
              <div
                key={video.video_id}
                className="flex items-center gap-3 rounded-lg border border-indigo-100 bg-white px-3 py-2"
              >
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-indigo-100 text-indigo-700">
                  <Loader2 className="h-4 w-4 animate-spin" />
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-gray-900">{video.title}</div>
                  <div className="text-xs text-gray-500">Downloading transcript or generating summary</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {queuedVideos.length > 0 && (
        <div className="space-y-2 rounded-xl border border-gray-100 bg-gray-50 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Queue
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {queuedVideos.map((video) => (
              <div
                key={video.video_id}
                className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2"
              >
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
                  {video.queue_position ?? 'Q'}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-gray-900">{video.title}</div>
                  <div className="text-xs text-gray-500">Waiting to start</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {completedVideos.length > 0 && (
        <div className="space-y-2 rounded-xl border border-emerald-100 bg-emerald-50/60 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
              Completed videos
            </div>
            <span className="text-xs font-medium text-emerald-700">
              {completedVideos.length} ready
            </span>
          </div>
          <div className="grid max-h-64 gap-2 overflow-y-auto sm:grid-cols-2">
            {completedVideos.map((video) => (
              <div
                key={video.video_id}
                className="flex items-center gap-3 rounded-lg border border-emerald-100 bg-white px-3 py-2"
              >
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
                  <Check className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-gray-900">{video.title}</div>
                  <div className="text-xs text-emerald-700">Ready</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        {stages.map((stage, index) => {
          const isStageComplete = index < currentStageIndex || currentStage === 'complete'
          const isStageCurrent = index === currentStageIndex && currentStage !== 'failed' && currentStage !== 'complete'
          const isStagePending = index > currentStageIndex || currentStage === 'failed'

          return (
            <motion.div
              key={stage.key}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.05 }}
              className="relative flex items-start gap-3 rounded-xl px-1 py-1"
            >
              {index < stages.length - 1 && (
                <div
                  className={cn(
                    'absolute left-4 top-8 h-8 w-0.5',
                    isStageComplete ? 'bg-emerald-400' : isStageCurrent ? 'bg-indigo-300' : 'bg-gray-200'
                  )}
                />
              )}
              <div
                className={cn(
                  'relative z-10 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border-2',
                  isStageComplete && 'border-emerald-500 bg-emerald-500 text-white',
                  isStageCurrent && 'border-indigo-600 bg-indigo-600 text-white',
                  isStagePending && 'border-gray-300 bg-white text-gray-300'
                )}
              >
                {isStageComplete ? (
                  <Check className="h-4 w-4" />
                ) : isStageCurrent ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <div className="h-2 w-2 rounded-full bg-gray-300" />
                )}
              </div>

              <div className="min-w-0 flex-1 pb-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className={cn('text-sm font-semibold', isStageComplete ? 'text-gray-900' : isStageCurrent ? 'text-gray-900' : 'text-gray-400')}>
                      {stage.label}
                    </div>
                    <div className="mt-0.5 text-xs text-gray-500">{stage.description}</div>
                  </div>
                  <div className="flex-shrink-0">
                    {isStageComplete && (
                      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                        Completed
                      </span>
                    )}
                    {isStageCurrent && (
                      <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                        Working
                      </span>
                    )}
                    {isStagePending && !isStageCurrent && (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500">
                        Pending
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </motion.div>
          )
        })}
      </div>

      {errors.length > 0 && (currentStage === 'failed' || currentStage === 'complete') && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-red-700">
            <AlertCircle className="h-4 w-4" />
            <span>Errors encountered</span>
          </div>
          <ul className="mt-2 space-y-1">
            {errors.map((error) => (
              <li key={error} className="text-xs text-red-600">
                {error}
              </li>
            ))}
          </ul>
        </div>
      )}

      {currentStage === 'complete' && failedCount > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <XCircle className="h-4 w-4" />
          Some videos failed, but the project finished. Use retry to process the failures again.
        </div>
      )}
    </div>
  )
}
