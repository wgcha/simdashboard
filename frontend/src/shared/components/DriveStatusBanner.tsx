import { CloudOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { driveApi } from '../api/drive'
import { ApiError } from '../api/errors'
import './DriveStatusBanner.css'

export const DRIVE_STATUS_POLL_MS = 60_000

const BANNER_TEXT: Record<string, string> = {
  UNAVAILABLE: '드라이브 연결이 원활하지 않습니다. 결과 원본을 읽거나 쓰는 기능이 지연될 수 있습니다.',
  AUTH_REQUIRED: '드라이브 인증이 필요합니다. 관리자에게 문의하세요.',
}

/** Polls GET /api/drive/status every 60 s; hidden in none mode and for OK/DEGRADED/UNKNOWN. */
export function DriveStatusBanner() {
  const [state, setState] = useState<string | null>(null)
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const controller = new AbortController()
    const poll = async () => {
      try {
        const status = await driveApi.userStatus(controller.signal)
        if (stopped) return
        setState(status.mode === 'scx' ? status.state ?? null : null)
        if (status.mode !== 'scx') return   // mode only changes with a restart: stop polling
      } catch (reason) {
        if (stopped || controller.signal.aborted) return
        if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) return
      }
      timer = window.setTimeout(() => { void poll() }, DRIVE_STATUS_POLL_MS)
    }
    void poll()
    return () => { stopped = true; controller.abort(); if (timer !== undefined) window.clearTimeout(timer) }
  }, [])
  const text = state ? BANNER_TEXT[state] : undefined
  if (!text) return null
  return <div className="drive-status-banner" role="status" aria-live="polite" data-testid="drive-status-banner" data-state={state ?? ''}>
    <CloudOff aria-hidden="true" /><span>{text}</span>
  </div>
}
