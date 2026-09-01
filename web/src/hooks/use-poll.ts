import { useCallback, useEffect, useRef, useState } from "react"

export interface PollState<T> {
  data: T | null
  error: string | null
  /** False once the first fetch has settled, whatever its outcome. */
  loading: boolean
}

/**
 * Poll one endpoint on an interval (D-32.6: a few seconds, no push channel).
 * Each tick aborts the previous in-flight request, so overlapping polls can
 * never race the render. A failed tick keeps the last good projection and
 * surfaces the error — a dashboard that hides its staleness invents liveness.
 */
export function usePoll<T>(
  fetchOne: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
) {
  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    loading: true,
  })
  const controllerRef = useRef<AbortController | null>(null)

  const tick = useCallback(async () => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      const data = await fetchOne(controller.signal)
      setState({ data, error: null, loading: false })
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return
      }

      const message = error instanceof Error ? error.message : String(error)
      setState((previous) => ({ ...previous, error: message, loading: false }))
    }
  }, [fetchOne])

  useEffect(() => {
    void tick()
    const timer = window.setInterval(() => void tick(), intervalMs)

    return () => {
      window.clearInterval(timer)
      controllerRef.current?.abort()
    }
  }, [tick, intervalMs])

  return { ...state, refresh: tick }
}
