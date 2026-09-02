import { useEffect, useState } from 'react'

export function useWorkspaceNotifications() {
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice((current) => current === notice ? '' : current), 4000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  return { error, notice, setError, setNotice }
}
