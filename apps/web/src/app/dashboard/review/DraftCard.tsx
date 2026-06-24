"use client"

import { useRef, useTransition } from 'react'
import { decideOnDraft } from './actions'

interface DraftCardProps {
  id: string
  sagaId: string
  persona: string
  targetPlatform: 'x' | 'linkedin' | 'telegram'
  generatedContent: string
  createdAt: string
  deadlineAt: string | null
  sagaVersion: number
}

function formatDeadline(deadlineAt: string | null): string {
  if (!deadlineAt) return 'No deadline'
  const deadline = new Date(deadlineAt)
  const now = new Date()
  const diffMs = deadline.getTime() - now.getTime()
  if (diffMs <= 0) return 'EXPIRED'
  const totalMinutes = Math.floor(diffMs / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours}h ${minutes}m remaining`
}

const platformColors: Record<string, string> = {
  x: 'bg-black text-white',
  linkedin: 'bg-blue-700 text-white',
  telegram: 'bg-sky-500 text-white',
}

export default function DraftCard({
  id,
  persona,
  targetPlatform,
  generatedContent,
  deadlineAt,
  sagaVersion,
}: DraftCardProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [isPending, startTransition] = useTransition()
  const [result, setResult] = [null as { ok: boolean; error?: string } | null, (v: { ok: boolean; error?: string } | null) => {
    // state is managed via ref trick below
    void v
  }]
  void result
  void setResult

  const deadlineLabel = formatDeadline(deadlineAt)
  const isExpired = deadlineLabel === 'EXPIRED'
  const badgeClass = platformColors[targetPlatform] ?? 'bg-gray-500 text-white'

  function handleDecision(decision: 'APPROVE' | 'REJECT') {
    const editedContent = textareaRef.current?.value ?? undefined
    startTransition(async () => {
      const res = await decideOnDraft(id, decision, editedContent)
      if (!res.ok) {
        alert(`Error: ${res.error ?? 'Unknown error'}`)
      }
    })
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white shadow-sm space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-bold px-2 py-0.5 rounded uppercase tracking-wide ${badgeClass}`}>
            {targetPlatform}
          </span>
          <span className="text-sm font-medium text-gray-800">{persona}</span>
          <span className="text-xs text-gray-400">v{sagaVersion}</span>
        </div>
        <span className={`text-xs font-mono ${isExpired ? 'text-red-600 font-bold' : 'text-amber-600'}`}>
          {deadlineLabel}
        </span>
      </div>

      {/* Content */}
      <div>
        <p className="text-xs text-gray-500 mb-1 font-medium">Generated content</p>
        <pre className="text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 border border-gray-100 rounded p-3 font-mono leading-relaxed max-h-48 overflow-y-auto">
          {generatedContent}
        </pre>
      </div>

      {/* Edit textarea */}
      <div>
        <p className="text-xs text-gray-500 mb-1 font-medium">Edit before approving (optional)</p>
        <textarea
          ref={textareaRef}
          defaultValue={generatedContent}
          rows={4}
          className="w-full text-sm font-mono border border-gray-200 rounded p-2 resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
          disabled={isPending}
        />
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={() => handleDecision('APPROVE')}
          disabled={isPending}
          className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white text-sm font-semibold py-2 px-4 rounded transition-colors"
        >
          {isPending ? 'Submitting…' : 'Approve'}
        </button>
        <button
          onClick={() => handleDecision('REJECT')}
          disabled={isPending}
          className="flex-1 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm font-semibold py-2 px-4 rounded transition-colors"
        >
          {isPending ? 'Submitting…' : 'Reject'}
        </button>
      </div>

      {/* Draft ID footer */}
      <p className="text-xs text-gray-300 font-mono">{id}</p>
    </div>
  )
}
