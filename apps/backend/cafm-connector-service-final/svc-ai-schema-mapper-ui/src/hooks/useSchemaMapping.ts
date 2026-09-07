import { useEffect, useRef, useCallback, useState } from 'react'
import { getSchemaMappingStatus } from '../api'
import type { SchemaMappingState } from '../types'

const POLL_INTERVAL_MS = 2000
const TERMINAL_STATUSES = new Set(['complete', 'error', 'ddl_failed', 'cancelled', 'step_paused', 'awaiting_review'])

export function useSchemaMapping(sessionId: string | null) {
  const [data, setData] = useState<SchemaMappingState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRef = useRef(true)

  const poll = useCallback(async () => {
    if (!sessionId || !activeRef.current) return

    try {
      const status = await getSchemaMappingStatus(sessionId)
      if (!activeRef.current) return
      setData(status)
      setError(null)

      if (!TERMINAL_STATUSES.has(status.status)) {
        timerRef.current = setTimeout(poll, POLL_INTERVAL_MS)
      }
    } catch (err: any) {
      if (!activeRef.current) return
      setError(err.message ?? 'Failed to fetch schema mapping status')
      timerRef.current = setTimeout(poll, POLL_INTERVAL_MS)
    }
  }, [sessionId])

  const refresh = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    poll()
  }, [poll])

  useEffect(() => {
    activeRef.current = true
    setData(null)
    setError(null)

    if (sessionId) {
      poll()
    }

    return () => {
      activeRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [sessionId, poll])

  return { data, error, refresh }
}
