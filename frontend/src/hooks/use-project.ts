import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useCallback, useEffect } from 'react'
import { projectsApi, synthesisApi, chatApi } from '@/lib/api'
import { useProjectStore } from '@/stores/project-store'
import type { Project, Video, CreateProjectRequest, ChatMessage } from '@/types'

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(0, 50),
    staleTime: 30_000,
  })
}

export function useProject(id: string | null) {
  const queryClient = useQueryClient()
  const setCurrentProject = useProjectStore((state) => state.setCurrentProject)
  const setVideos = useProjectStore((state) => state.setVideos)
  const setProcessingStatus = useProjectStore((state) => state.setProcessingStatus)

  const projectQuery = useQuery({
    queryKey: ['project', id],
    queryFn: () => (id ? projectsApi.get(id) : Promise.reject('No project ID')),
    enabled: !!id,
    staleTime: 60_000,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s && ['pending', 'processing', 'fetching', 'embedding'].includes(s) ? 5000 : false
    },
  })

  const videosQuery = useQuery({
    queryKey: ['project-videos', id],
    queryFn: () => (id ? projectsApi.getVideos(id) : Promise.reject('No project ID')),
    enabled: !!id,
    staleTime: 60_000,
  })

  const statusQuery = useQuery({
    queryKey: ['project-status', id],
    queryFn: () => (id ? projectsApi.getStatus(id) : Promise.reject('No project ID')),
    // Fetch once for completed projects too, so the UI can expose post-processing
    // problems such as an incomplete embeddings index.
    enabled: !!id && ['pending', 'processing', 'fetching', 'embedding', 'failed', 'completed'].includes(projectQuery.data?.status ?? ''),
    refetchInterval: (query) => {
      const stage = query.state.data?.stage
      return stage !== 'complete' && stage !== 'failed' ? 2000 : false
    },
  })

  // Sync to store (must be in effects, never during render)
  useEffect(() => {
    if (projectQuery.data) setCurrentProject(projectQuery.data)
  }, [projectQuery.data, setCurrentProject])

  useEffect(() => {
    if (videosQuery.data) setVideos(videosQuery.data)
  }, [videosQuery.data, setVideos])

  useEffect(() => {
    if (statusQuery.data) setProcessingStatus(statusQuery.data)
  }, [statusQuery.data, setProcessingStatus])

  return {
    project: projectQuery.data,
    videos: videosQuery.data,
    status: statusQuery.data,
    isLoading: projectQuery.isLoading || videosQuery.isLoading,
    isError: projectQuery.isError || videosQuery.isError,
    error: projectQuery.error || videosQuery.error,
    refetch: () => {
      projectQuery.refetch()
      videosQuery.refetch()
      statusQuery.refetch()
    },
  }
}

export function useCreateProject() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: CreateProjectRequest) => projectsApi.create(data),
    onSuccess: (newProject) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.setQueryData(['project', newProject.id], newProject)
    },
  })
}

export function useDeleteProject() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => projectsApi.delete(id),
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.removeQueries({ queryKey: ['project', deletedId] })
    },
  })
}

export function useSynthesis(projectId: string | null) {
  const query = useQuery({
    queryKey: ['synthesis', 'consolidated', projectId],
    queryFn: () =>
      projectId ? synthesisApi.getConsolidated(projectId) : Promise.reject('No project ID'),
    enabled: !!projectId,
    staleTime: 300_000,
    retry: false,
  })
  return { synthesis: query.data, isLoading: query.isLoading, isError: query.isError }
}

export function useSummaries(projectId: string | null) {
  const query = useQuery({
    queryKey: ['synthesis', 'summaries', projectId],
    queryFn: () =>
      projectId ? synthesisApi.getSummaries(projectId) : Promise.reject('No project ID'),
    enabled: !!projectId,
    staleTime: 300_000,
    retry: false,
  })
  return { summaries: query.data, isLoading: query.isLoading, isError: query.isError }
}

export function useChatMessages(projectId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)

  const sendMessage = useCallback(async (text: string, videoIds?: string[]) => {
    if (!projectId || isSending) return

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      sources: [],
    }
    setMessages((prev) => [...prev, userMsg])
    setIsSending(true)

    const assistantId = crypto.randomUUID()
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      sources: [],
    }
    setMessages((prev) => [...prev, assistantMsg])

    await chatApi.streamMessage(
      { project_id: projectId, message: text, video_ids: videoIds },
      (chunk) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk } : m))
        )
      },
      (sources) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, sources } : m))
        )
      },
      () => {}
    )

    setIsSending(false)
  }, [projectId, isSending])

  return { messages, sendMessage, isSending }
}

export function useRegenerateSynthesis() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (projectId: string) => synthesisApi.regenerate(projectId),
    onSuccess: (_, projectId) => {
      queryClient.invalidateQueries({ queryKey: ['synthesis', 'consolidated', projectId] })
      queryClient.invalidateQueries({ queryKey: ['synthesis', 'summaries', projectId] })
    },
  })
}
