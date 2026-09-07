import { useEffect, useRef, useCallback, useState } from 'react'
import { getMigrationStatus } from '../api'
import type { MigrationState } from '../types'

const POLL_INTERVAL_MS = 2000

const TERMINAL_STATUSES = new Set(['complete', 'failed', 'ddl_failed', 'cancelled'])

export function useMigration(migrationId: string | null) {
  const [data, setData] = useState<MigrationState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRef = useRef(true)

  const poll = useCallback(async () => {
    if (!migrationId || !activeRef.current) return

    try {
      const status = await getMigrationStatus(migrationId)
      if (!activeRef.current) return
      setData(status)
      setError(null)

      // Stop polling on terminal states
      if (!TERMINAL_STATUSES.has(status.status)) {
        timerRef.current = setTimeout(poll, POLL_INTERVAL_MS)
      }
    } catch (err: any) {
      if (!activeRef.current) return
      setError(err.message ?? 'Failed to fetch status')
      // Retry even on error
      timerRef.current = setTimeout(poll, POLL_INTERVAL_MS)
    }
  }, [migrationId])

  // Force an immediate re-poll (call after submitting a gate/advance)
  const refresh = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    poll()
  }, [poll])

  useEffect(() => {
    activeRef.current = true
    setData(null)
    setError(null)

    if (migrationId) {
      poll()
    }

    return () => {
      activeRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [migrationId, poll])

  return { data, error, refresh }
}
