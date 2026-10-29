import { useEffect, useState } from 'react'
import { simulationDashboardApi, type DashboardVideoRule } from '../../shared/api/simulationDashboard'
import { Button } from '../../shared/components/Button'
import { changedAtText, keywordProblem, parseKeywords } from './videoRepresentative'
import './VideoRepresentative.css'

export type VideoRuleEditorProps = {
  projectId: string
  rule: DashboardVideoRule
  /** The rule was saved: the grid reloads so the new defaults apply. */
  onSaved: (rule: DashboardVideoRule) => void
}

/** 「대표 영상 자동 선택 규칙」: ordered file-name keywords of the project (admin / project manager can edit). */
export function VideoRuleEditor({ projectId, rule, onSaved }: VideoRuleEditorProps) {
  const [text, setText] = useState(rule.keywords.join(', '))
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  useEffect(() => { setText(rule.keywords.join(', ')) }, [rule.keywords.join('\n')]) // eslint-disable-line react-hooks/exhaustive-deps
  const words = parseKeywords(text)
  const problem = keywordProblem(text)
  const unchanged = words.join('\n') === rule.keywords.join('\n')
  const canEdit = Boolean(rule.can_edit_rule)
  const save = async () => {
    setBusy(true); setNotice(null)
    try {
      const saved = await simulationDashboardApi.saveVideoRule(projectId, words)
      setNotice({ text: '규칙을 저장했습니다. 이 Case에 적용합니다.', error: false })
      onSaved({ ...rule, ...saved })
    } catch (reason) { setNotice({ text: reason instanceof Error && reason.message ? reason.message : '규칙을 저장하지 못했습니다.', error: true }) }
    finally { setBusy(false) }
  }
  return <details className="rep-rule" data-testid="video-rule-editor">
    <summary>대표 영상 자동 선택 규칙 (프로젝트별){rule.keywords.length ? ` · ${rule.keywords.join(' → ')}` : ''}</summary>
    <div className="rep-rule__body">
      <label className="rep-rule__field"><span>파일 이름에 이 단어가 있으면 대표로 (쉼표로 구분, 앞쪽이 우선)</span>
        <input type="text" value={text} placeholder="예: Max_Stress, Mid" disabled={!canEdit || busy} onChange={(event) => setText(event.target.value)} aria-invalid={Boolean(problem)} />
      </label>
      {words.length ? <ol className="rep-rule__chips" aria-label="적용 순서">{words.map((word) => <li key={word.toLowerCase()}>{word}</li>)}</ol> : null}
      <p className="rep-rule__hint">규칙에 맞는 영상이 없으면 파일 이름순 첫 번째 · 사용자가 직접 고른 대표가 우선합니다.</p>
      {problem && <p className="rep-panel__notice error" role="alert">{problem}</p>}
      <div className="rep-rule__actions">
        <Button size="sm" variant="primary" disabled={!canEdit || busy || Boolean(problem) || unchanged} onClick={() => void save()}>규칙 저장 · 이 Case에 적용</Button>
        <span className="rep-panel__meta">{canEdit ? (rule.updated_at ? `마지막 변경: ${rule.updated_by_name || rule.updated_by || '알 수 없음'} · ${changedAtText(rule.updated_at)}` : '') : '규칙은 프로젝트 관리자만 바꿀 수 있습니다.'}</span>
        {notice && <span className={`rep-panel__notice${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</span>}
      </div>
    </div>
  </details>
}
