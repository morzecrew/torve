import { useEffect, useState } from "react"

/** The current wall-clock instant, ticking so relative ages stay live
 * between polls — the projected-at stamp's age must move even when the
 * projection does not (D-32.6). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])

  return now
}
