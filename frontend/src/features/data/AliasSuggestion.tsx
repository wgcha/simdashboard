import { Check, LoaderCircle, Search, X } from 'lucide-react'
import { useAliasSuggestions } from './useAliasSuggestions'
import { useEffect } from 'react'
import type { SemanticVocabularyEntry, VocabularyTargetKind } from '../../shared/api/semanticVocabulary'

const kindLabels: Record<VocabularyTargetKind, string> = { FOLDER_ROLE: '폴더 역할', PROJECT: '프로젝트', REQUEST: '의뢰', LOAD_CASE: '하중 경우', RESULT_ITEM: '결과 항목' }

export function AliasSuggestion({ term, targetKinds, scopeProjectId, onSelect, label = '별칭 찾기' }: {
  term: string
  targetKinds: VocabularyTargetKind[]
  scopeProjectId?: string
  onSelect: (entry: SemanticVocabularyEntry) => void
  label?: string
}) {
  const { clear, confirm, error, loading, match, suggest } = useAliasSuggestions()
  useEffect(() => { clear() }, [clear, scopeProjectId, targetKinds.join('|'), term])
  const search = () => { if (term.trim()) void suggest([term], targetKinds, scopeProjectId) }
  return <div className="alias-suggestion">
    <button type="button" className="alias-suggestion-button" onClick={search} disabled={loading || !term.trim()} aria-label={`${label}: ${term}`}><Search />{loading ? <LoaderCircle className="spin" /> : label}</button>
    {match ? <div className="alias-suggestion-popover" role="status">
      <div className="alias-suggestion-head"><span>{match.status === 'UNMAPPED' ? '일치하는 별칭 없음' : `${match.candidates.length}개 후보`}</span><button type="button" aria-label="별칭 후보 닫기" onClick={clear}><X /></button></div>
      {match.candidates.length ? <div className="alias-suggestion-candidates">{match.candidates.map((candidate) => <button type="button" key={`${candidate.id}-${candidate.target_id}`} onClick={() => { void confirm(candidate).then((valid) => { if (valid) { onSelect(candidate); clear() } }) }}><span><strong>{candidate.label}</strong><small>{kindLabels[candidate.target_kind]} · {candidate.target_label}</small></span><Check /></button>)}</div> : <p>관리자가 기준 정의에 이 용어를 등록하면 다시 찾을 수 있습니다.</p>}
    </div> : null}
    {error ? <span className="alias-suggestion-error" role="alert">{error}</span> : null}
  </div>
}
