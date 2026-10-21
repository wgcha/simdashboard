import { RefreshCw, Tag } from 'lucide-react'
import { useEffect } from 'react'

import { buildLine, releaseDateText, versionLabel } from '../../shared/api/releasesModel'
import { formatServerDate } from '../../shared/api/serverTime'
import { refreshReleases, useReleasesSnapshot } from './releasesStore'
import './releases.css'

/** ``/workspace/releases``: running version, build info and every RELEASES.md entry, newest first (read-only). */
export function ReleaseBoard() {
  const { info, stale, unavailable } = useReleasesSnapshot()
  useEffect(() => { void refreshReleases() }, [])
  const build = info ? buildLine(info.build, formatServerDate) : ''

  return <section className="release-board" aria-labelledby="release-board-title" data-testid="release-board">
    <header className="release-board__head">
      <div>
        <span className="release-board__eyebrow"><Tag aria-hidden="true" />RELEASES</span>
        <h1 id="release-board-title">버전 게시판</h1>
        <p>배포된 버전과 주요 기능입니다. 서버에 새 버전을 올릴 때만 갱신되며, 화면에서는 고칠 수 없습니다.</p>
      </div>
      {info && <dl className="release-board__current" aria-label="현재 서버 버전">
        <div><dt>서버 버전</dt><dd data-testid="release-board-current">{versionLabel(info.current)}</dd></div>
        {build && <div><dt>{info.build.source === 'git' ? '소스 기준' : '빌드'}</dt><dd>{build}</dd></div>}
      </dl>}
    </header>
    {stale && <p className="release-board__notice" role="status">
      새 버전이 배포되었습니다 — 이 화면은 이전 버전으로 열려 있습니다.
      <button type="button" onClick={() => window.location.reload()}><RefreshCw aria-hidden="true" />새로고침</button>
    </p>}
    {unavailable && <p className="release-board__error" role="alert">버전 정보를 볼 권한이 없습니다. 다시 로그인하세요.</p>}
    {!info && !unavailable && <p className="release-board__empty">불러오는 중입니다.</p>}
    {info && info.releases.length === 0 && <p className="release-board__empty">등록된 릴리스 기록이 없습니다.</p>}
    {info && info.releases.length > 0 && <ol className="release-board__list" aria-label="릴리스 목록">
      {info.releases.map((release) => <li key={release.version} className={`release-board__item${release.version === info.current ? ' is-current' : ''}`} data-testid="release-entry">
        <header>
          <h2>{versionLabel(release.version)}</h2>
          <time dateTime={release.date}>{releaseDateText(release.date)}</time>
          {release.version === info.current && <span className="release-board__tag">현재 버전</span>}
        </header>
        {release.notes.length > 0 ? <ul>{release.notes.map((note, index) => <li key={index}>{note}</li>)}</ul> : <p className="release-board__empty-notes">기록된 주요 기능이 없습니다.</p>}
      </li>)}
    </ol>}
  </section>
}
