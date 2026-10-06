import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { AlertTriangle, Info } from 'lucide-react'
import type { DashboardNameWarning } from '../../shared/api/simulationDashboard'
import './FolderNameWarnings.css'

/** Path from the request's Working (or Final) folder; the full path stays in the tooltip. */
export function shortWarningPath(path: string) {
  const index = path.search(/\/(Working|Final)\//i)
  return index >= 0 ? path.slice(index + 1) : path
}

/**
 * W6: folder name slips (users copy folders into SPDM by hand). Only reports;
 * folders are never changed from this screen.
 */
export function FolderNameWarnings({ warnings }: { warnings?: DashboardNameWarning[] | null }) {
  if (!warnings?.length) return null
  const hasWarning = warnings.some((item) => item.severity === 'warning')
  return <details className={`case-name-warnings${hasWarning ? '' : ' case-name-warnings--info'}`} data-testid="folder-name-warnings">
    <summary title="폴더 이름이 비슷하거나 규칙과 달라 결과가 나뉘거나 Case 비교가 맞지 않을 수 있습니다. 폴더는 자동으로 고치지 않습니다."><AlertTriangle aria-hidden="true" />폴더 이름 확인 {warnings.length}건</summary>
    <div className="case-name-warnings__panel" role="region" aria-label="폴더 이름 확인 목록">
      <p className="case-name-warnings__note">SPDM 폴더에서 이름을 직접 고친 뒤 새로 고치세요. 이 화면은 폴더를 바꾸지 않습니다.</p>
      <ul>{warnings.map((item, index) => <li key={`${item.kind}:${index}`} className={item.severity === 'warning' ? 'is-warning' : 'is-info'}>
        <span className="case-name-warnings__message">{item.severity === 'warning' ? <AlertTriangle aria-label="경고" /> : <Info aria-label="참고" />}{item.message}</span>
        <ul className="case-name-warnings__paths" aria-label="관련 폴더">{item.paths.map((path) => <li key={path} title={path}>{shortWarningPath(path)}</li>)}</ul>
      </li>)}</ul>
    </div>
  </details>
}

const SceneWarningContext = createContext<Map<string, string[]>>(new Map())

/** Scene folder name -> warning messages, for marking Scenes in the Scene 비교 tab. */
export function SceneNameWarningProvider({ warnings, runOptionId, children }: { warnings?: DashboardNameWarning[] | null; runOptionId?: string; children: ReactNode }) {
  const byName = useMemo(() => {
    const map = new Map<string, string[]>()
    // A sibling warning of another Run Option must not mark a same-named Scene here.
    for (const item of (warnings ?? []).filter((warning) => !warning.run_option_id || !runOptionId || warning.run_option_id === runOptionId)) {
      for (const path of item.paths) {
        const name = path.split('/').pop() ?? ''
        if (name) map.set(name, [...(map.get(name) ?? []), item.message])
      }
    }
    return map
  }, [runOptionId, warnings])
  return <SceneWarningContext.Provider value={byName}>{children}</SceneWarningContext.Provider>
}

/** Small icon next to a Scene label; comparison labels read "Case · Scene". */
export function SceneNameWarningIcon({ label }: { label: string }) {
  const byName = useContext(SceneWarningContext)
  if (!byName.size) return null
  const name = byName.has(label) ? label : label.includes(' · ') ? label.slice(label.lastIndexOf(' · ') + 3) : ''
  const messages = byName.get(name)
  if (!messages) return null
  const text = `폴더 이름 확인: ${Array.from(new Set(messages)).join(' / ')}`
  return <span className="case-scene-name-warning" role="img" aria-label={text} title={text} data-testid="scene-name-warning"><AlertTriangle aria-hidden="true" /></span>
}
