import { useState, useEffect, useCallback, useRef } from 'react'
import { useToast } from '../store/toast'

/**
 * useApi(fetcher, deps?)
 * Fires fetcher() on mount (and when deps change).
 * Returns { data, loading, error, reload }
 */
export function useApi(fetcher, deps = []) {
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)
  const toast = useToast()
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetcher()
      if (mountedRef.current) setData(result)
    } catch (err) {
      if (mountedRef.current) {
        setError(err.message)
        toast(err.message, 'error')
      }
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    mountedRef.current = true
    load()
    return () => { mountedRef.current = false }
  }, [load])

  return { data, loading, error, reload: load }
}

/**
 * useAction(fn)
 * Wraps an async action with loading state + auto toast on error.
 * Returns [run, loading]
 */
export function useAction(fn) {
  const [loading, setLoading] = useState(false)
  const toast = useToast()

  const run = useCallback(async (...args) => {
    setLoading(true)
    try {
      const result = await fn(...args)
      return result
    } catch (err) {
      toast(err.message || 'حدث خطأ', 'error')
      return null
    } finally {
      setLoading(false)
    }
  }, [fn, toast])

  return [run, loading]
}
