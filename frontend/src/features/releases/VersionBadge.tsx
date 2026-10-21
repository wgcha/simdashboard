import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { buildLine, versionLabel } from '../../shared/api/releasesModel'
import { formatServerDate } from '../../shared/api/serverTime'
import { useReleasesPolling, useReleasesSnapshot } from './releasesStore'
import './releases.css'

export const RELEASES_PATH = '/workspace/releases'

/**
 * Top bar version button (`v0.3.0`) → 버전 게시판.  It also polls the server, and when a deployment
 * changed the server under this open page it adds a small 새로고침 notice next to the button.
 */
export function VersionBadge() {
  const navigate = useNavigate()
  useReleasesPolling()
  const { info, stale, unavailable } = useReleasesSnapshot()
  if (unavailable || !info) return null
  const build = buildLine(info.build, formatServerDate)
  const title = `버전 게시판 열기${build ? ` · ${build}` : ''}`
  return <div className="version-badge">
    <button type="button" className="version-badge__button" data-testid="version-badge" aria-label={`버전 ${versionLabel(info.current)}, 버전 게시판 열기`} title={title}
      onClick={() => navigate(RELEASES_PATH)}>{versionLabel(info.current)}</button>
    {stale && <span className="version-badge__notice" role="status" data-testid="version-notice">
      새 버전이 배포되었습니다
      <button type="button" onClick={() => window.location.reload()}><RefreshCw aria-hidden="true" />새로고침</button>
    </span>}
  </div>
}
