'use client'

import { useState, useEffect, useCallback, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Search as SearchIcon, Loader2, SlidersHorizontal, X } from 'lucide-react'
import { AppHeader } from '@/components/AppHeader'
import { searchApi } from '@/lib/api'

interface YouTubeVideo {
  id: string
  title: string
  thumbnail_url: string
  channel: string
  duration_seconds: number
  published_at?: string
  relevance_score?: number
  pre_selected?: boolean
}

const PAGE_SIZE = 20

function NewProjectPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryParam = searchParams.get('q')

  const [title, setTitle] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMode, setSearchMode] = useState<'smart' | 'direct'>('smart')
  const [isSearching, setIsSearching] = useState(false)
  const [selectedVideos, setSelectedVideos] = useState<YouTubeVideo[]>([])
  const [searchTerms, setSearchTerms] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(0)
  const [allSearchResults, setAllSearchResults] = useState<YouTubeVideo[]>([])
  const [isCreating, setIsCreating] = useState(false)
  const [showTitleModal, setShowTitleModal] = useState(false)

  useEffect(() => {
    if (queryParam) {
      setSearchQuery(queryParam)
      performSearch(queryParam)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const performSearch = useCallback(async (query: string) => {
    if (!query.trim()) return
    setIsSearching(true)
    setError(null)
    setSearchTerms([])
    try {
      const data = await searchApi.searchVideos(query, searchMode, 0, 96)
      const videos: YouTubeVideo[] = data.videos || []
      setAllSearchResults(videos)
      setSearchTerms(data.search_terms || [])
      setPage(0)
      const preSelected = videos.filter((v) => v.pre_selected)
      if (preSelected.length > 0) {
        setSelectedVideos((prev) => {
          const existingIds = new Set(prev.map((v) => v.id))
          const newOnes = preSelected.filter((v) => !existingIds.has(v.id))
          return [...prev, ...newOnes]
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to search videos')
      setAllSearchResults([])
    } finally {
      setIsSearching(false)
    }
  }, [searchMode])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    performSearch(searchQuery)
  }

  const totalPages = Math.max(1, Math.ceil(allSearchResults.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages - 1)
  const searchResults = allSearchResults.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE)

  const toggleVideoSelection = (video: YouTubeVideo) => {
    const isSelected = selectedVideos.some((v) => v.id === video.id)
    if (isSelected) {
      setSelectedVideos(selectedVideos.filter((v) => v.id !== video.id))
    } else {
      setSelectedVideos([...selectedVideos, video])
    }
  }

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const handleCreateProject = async () => {
    if (!title.trim()) {
      setError('Please enter a project title')
      return
    }
    if (selectedVideos.length === 0) {
      setError('Please select at least one video')
      return
    }
    setIsCreating(true)
    setError(null)
    try {
      const response = await fetch(`/api/v1/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_ids: selectedVideos.map((v) => v.id),
          name: title.trim(),
          query: searchQuery.trim() || title.trim(),
        }),
      })
      if (!response.ok) throw new Error('Failed to create project')
      const project = await response.json()
      router.push(`/projects/${project.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create project')
      setIsCreating(false)
    }
  }

  return (
    <div className="min-h-screen bg-white">
      <AppHeader />

      {/* Title Modal */}
      {showTitleModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Name your project</h2>
              <button onClick={() => setShowTitleModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="h-5 w-5" />
              </button>
            </div>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { setShowTitleModal(false); handleCreateProject() } }}
              autoFocus
              placeholder="e.g. Kubernetes in Production"
              className="w-full px-3 py-2.5 rounded-lg border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900 mb-4"
            />
            {error && <p className="text-sm text-red-500 mb-3">{error}</p>}
            <button
              onClick={() => { setShowTitleModal(false); handleCreateProject() }}
              disabled={!title.trim() || isCreating}
              className="w-full bg-gray-900 text-white font-medium py-2.5 rounded-lg text-sm hover:bg-gray-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
            >
              {isCreating ? <><Loader2 className="h-4 w-4 animate-spin" /> Creating...</> : 'Create Project & Start Processing'}
            </button>
          </div>
        </div>
      )}

      <main className="max-w-screen-lg mx-auto px-6 py-8">
        {/* Page header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Video Search</h1>
          <p className="text-sm text-gray-500 mt-1">Search for YouTube videos to add to your project</p>
        </div>

        {/* Search bar */}
        <form onSubmit={handleSearch} className="mb-4">
          <div className="flex items-center border border-gray-200 rounded-lg bg-white shadow-sm overflow-hidden">
            <SearchIcon className="h-4 w-4 text-gray-400 ml-4 flex-shrink-0" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search YouTube videos..."
              className="flex-1 px-3 py-3 text-sm focus:outline-none bg-transparent"
            />
            {searchQuery && (
              <button type="button" onClick={() => { setSearchQuery(''); setAllSearchResults([]) }} className="mr-2 p-1 text-gray-400 hover:text-gray-600">
                <X className="h-4 w-4" />
              </button>
            )}
            {isSearching && <Loader2 className="h-4 w-4 text-gray-400 animate-spin mr-3" />}
            <button
              type="submit"
              disabled={!searchQuery.trim() || isSearching}
              className="mr-1.5 rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Search
            </button>
          </div>
        </form>

        <div className="mb-5 flex items-center gap-2">
          <span className="text-xs text-gray-500">Search mode:</span>
          <div className="flex gap-0.5 rounded-md bg-gray-100 p-0.5">
            {(['smart', 'direct'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={searchMode === mode}
                onClick={() => setSearchMode(mode)}
                className={`rounded px-2.5 py-1 text-xs font-medium capitalize transition-colors
                  ${searchMode === mode ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
              >
                {mode}
              </button>
            ))}
          </div>
          <span className="text-xs text-gray-400">
            {searchMode === 'smart' ? 'AI-ranked results' : 'YouTube results'}
          </span>
        </div>

        {error && (
          <div role="alert" className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Results meta + filters */}
        {allSearchResults.length > 0 && (
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm text-gray-500">About {allSearchResults.length.toLocaleString()} results</p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 text-sm text-gray-600 border border-gray-200 rounded-md px-3 py-1.5 hover:bg-gray-50"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
                Filters
              </button>
            </div>
          </div>
        )}

        {/* Results list */}
        {searchResults.length > 0 ? (
          <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden bg-white mb-20">
            {searchResults.map((video) => {
              const isSelected = selectedVideos.some((v) => v.id === video.id)
              return (
                <div
                  key={video.id}
                  onClick={() => toggleVideoSelection(video)}
                  className={`flex items-center gap-4 px-5 py-4 cursor-pointer transition-colors
                    ${isSelected ? 'bg-indigo-50/60' : 'hover:bg-gray-50'}`}
                >
                  {/* Thumbnail */}
                  <div className="relative flex-shrink-0 w-32 h-20 rounded-lg overflow-hidden bg-gray-100">
                    <img
                      src={video.thumbnail_url}
                      alt={video.title}
                      className="w-full h-full object-cover"
                    />
                    <div className="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
                      {formatDuration(video.duration_seconds)}
                    </div>
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <h3 className="font-medium text-gray-900 text-sm line-clamp-2 mb-0.5">{video.title}</h3>
                    <p className="text-xs text-gray-500">{video.channel} · {formatDuration(video.duration_seconds)}</p>
                  </div>

                  {/* Checkbox */}
                  <div className={`w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors
                    ${isSelected ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300 bg-white'}`}>
                    {isSelected && (
                      <svg className="h-3 w-3 text-white" viewBox="0 0 12 12" fill="none">
                        <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </div>
                </div>
              )
            })}

            {/* Pagination */}
            {allSearchResults.length > PAGE_SIZE && (
              <div className="flex items-center justify-center gap-3 py-4 bg-gray-50">
                <button
                  type="button"
                  onClick={() => setPage(p => p - 1)}
                  disabled={currentPage === 0}
                  className="text-sm text-gray-600 border border-gray-200 rounded-md px-3 py-1.5 hover:bg-white disabled:opacity-40"
                >
                  ← Previous
                </button>
                <span className="text-sm text-gray-500">Page {currentPage + 1} of {totalPages}</span>
                <button
                  type="button"
                  onClick={() => setPage(p => p + 1)}
                  disabled={currentPage + 1 >= totalPages}
                  className="text-sm text-gray-600 border border-gray-200 rounded-md px-3 py-1.5 hover:bg-white disabled:opacity-40"
                >
                  Next →
                </button>
              </div>
            )}
          </div>
        ) : !isSearching && searchQuery && (
          <div className="text-center py-16 text-gray-400">
            <p className="text-sm">No results found for "{searchQuery}"</p>
          </div>
        )}

        {/* Empty initial state */}
        {!isSearching && !searchQuery && allSearchResults.length === 0 && (
          <div className="text-center py-12 text-gray-400">
            <SearchIcon className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm">Search YouTube to find videos for your project</p>
            <div className="flex flex-wrap justify-center gap-2 mt-4">
              {['machine learning', 'web development', 'AI tutorials', 'React hooks'].map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => { setSearchQuery(s); performSearch(s) }}
                  className="text-xs px-3 py-1.5 rounded-full bg-gray-100 hover:bg-gray-200 text-gray-600 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* Sticky footer CTA — only visible when videos selected */}
      {selectedVideos.length > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-white border-t border-gray-200 shadow-lg">
          <div className="max-w-screen-lg mx-auto px-6 py-4 flex items-center justify-between">
            <span className="text-sm font-medium text-gray-700">
              {selectedVideos.length} video{selectedVideos.length !== 1 ? 's' : ''} selected
            </span>
            <button
              onClick={() => setShowTitleModal(true)}
              className="inline-flex items-center gap-2 bg-gray-900 text-white font-medium px-5 py-2.5 rounded-lg text-sm hover:bg-gray-700 transition-colors"
            >
              Add {selectedVideos.length} video{selectedVideos.length !== 1 ? 's' : ''} to project →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function NewProjectPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="w-8 h-8 border-[3px] border-gray-900 border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <NewProjectPageContent />
    </Suspense>
  )
}
