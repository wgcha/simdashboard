import { AlertTriangle, CheckCircle2, LoaderCircle, ShieldAlert } from 'lucide-react'
import type { ImpactResponse } from '../../shared/api/semanticImpact'
import './SemanticImpactDialog.css'

type Props = { impact: ImpactResponse | null; loading: boolean; confirming: boolean; onClose: () => void; onConfirm: () => void }

function statusLabel(status: ImpactResponse['validation']['status']) {
  if (status === 'READY') return '활성화 가능'
  if (status === 'UNVERIFIED') return '검증되지 않은 참조 있음'
  return '활성화 차단'
}

const classificationLabels: Record<string, string> = {
  UNCHANGED: '변경 없음',
  PRESENTATION_ONLY: '표시만 변경',
  INTERPRETATION: '해석 변경',
  UNKNOWN: '확인 불가',
}

const reasonLabels: Record<string, string> = {
  ACTIVE_PAIR_INCOMPLETE: '활성 레시피·템플릿 쌍이 완전하지 않습니다.',
  BINDING_RECIPE_INVALID: '기존 폴더의 레시피가 유효하지 않습니다.',
  BINDING_SCAN_TRUNCATED: '영향 폴더 목록이 제한되어 일부만 확인되었습니다.',
  ITEM_SNAPSHOT_MEANING_CHANGED: '결과 항목의 의미가 바뀌었습니다.',
  PRESENTATION_FIELDS_CHANGED: '표시 설정만 바뀌었습니다.',
  RECIPE_DEFINITION_UNREADABLE: '레시피 정의를 읽을 수 없습니다.',
  RECIPE_MAPPING_OR_PARSING_CHANGED: '필드 매핑 또는 파일 해석 방식이 바뀌었습니다.',
  RECIPE_NOT_ACTIVE: '레시피가 현재 활성 상태가 아닙니다.',
  SAMPLE_REQUIRED: '검증용 저장 샘플이 필요합니다.',
  SNAPSHOT_UNREADABLE: '저장된 항목 스냅샷을 읽을 수 없습니다.',
  VERSION_NOT_FOUND: '요청한 레시피 또는 템플릿 버전을 찾을 수 없습니다.',
  WIDGET_INPUT_INVALID: '위젯 입력 호환성 검증에 실패했습니다.',
}

function reasonLabel(code?: string | null) {
  if (!code) return ''
  return reasonLabels[code] ?? code.replaceAll('_', ' ').toLowerCase()
}

function checkLabel(check: { reason_code?: string | null; status?: string }) {
  if (check.reason_code) return reasonLabel(check.reason_code)
  if (check.status === 'READY') return '호환됨'
  if (check.status === 'BLOCK') return '호환되지 않음'
  return '검증 항목'
}

function pairIdentity(check: ImpactResponse['affected_bindings'][number]['compatibility'][number]) {
  const recipe = check.recipe_id ? `레시피 ${check.recipe_id}${check.recipe_version == null ? '' : ` · v${check.recipe_version}`}` : '레시피 없음'
  const template = check.template_id ? `템플릿 ${check.template_id}${check.template_version == null ? '' : ` · v${check.template_version}`}` : '템플릿 없음'
  return `${recipe} / ${template}`
}

export function SemanticImpactDialog({ impact, loading, confirming, onClose, onConfirm }: Props) {
  const status = impact?.validation.status ?? 'BLOCK'
  return <div className="semantic-dialog-backdrop" role="presentation">
    <section className="semantic-dialog semantic-impact-dialog" role="dialog" aria-modal="true" aria-labelledby="semantic-impact-heading">
      <div className="semantic-card-heading"><div><span>ACTIVATION IMPACT REVIEW</span><h2 id="semantic-impact-heading">활성화 영향 미리보기</h2></div><button type="button" className="icon-button" aria-label="닫기" onClick={onClose}>×</button></div>
      {loading ? <div className="semantic-impact-body"><div className="empty-preview"><LoaderCircle className="spin" /> 저장된 정의와 참조를 검증하는 중입니다.</div></div> : impact ? <>
        <div className="semantic-impact-body">
          <div className={`semantic-impact-status ${status.toLowerCase()}`}><span className="semantic-impact-status-icon">{status === 'READY' ? <CheckCircle2 /> : <ShieldAlert />}</span><strong>{statusLabel(status)}</strong><span>{classificationLabels[impact.recipe_change.classification] ?? impact.recipe_change.classification}</span><span>{impact.recipe_change.reason_codes.map(reasonLabel).filter(Boolean).join(' · ') || '특이 사유 없음'}</span>{impact.validation.truncated ? <span>검증 목록이 일부만 표시됩니다.</span> : null}</div>
          <div className="semantic-impact-summary"><span>영향받는 폴더<strong>{impact.affected_bindings.length}</strong></span><span>보호된 과거 Run<strong>{impact.protected_prior_runs.count}</strong></span><span>검토 대기 파일<strong>{impact.pending_review_items.count ?? '확인 불가'}</strong></span><span>검증 상태<strong>{statusLabel(status)}</strong></span></div>
          <div className="semantic-impact-section"><h3>영향 받는 폴더 바인딩</h3><div className="semantic-impact-list">{impact.affected_bindings.length ? impact.affected_bindings.map((binding) => <div className="semantic-impact-row" key={`${binding.id}:${binding.revision}`}><span><strong>{binding.relative_path}</strong><small>개정 {binding.revision} · 레시피 {binding.recipe_ids.join(', ') || '없음'}</small></span><div className="semantic-impact-reason">{binding.compatibility.length ? binding.compatibility.map((check, index) => <span className={`semantic-impact-compatibility ${check.status.toLowerCase()}`} key={`${checkLabel(check)}:${index}`}><strong>{check.status === 'READY' ? '호환됨' : '호환 차단'}</strong><small>{pairIdentity(check)}</small>{check.reason_code ? <em>{reasonLabel(check.reason_code)}</em> : null}</span>) : <span>영향 없음</span>}</div></div>) : <small>영향 받는 바인딩이 없습니다.</small>}</div></div>
          <div className="semantic-impact-section"><h3>검증 결과</h3><ul className="semantic-impact-checks">{impact.validation.checks.map((check, index) => <li key={`${checkLabel(check)}:${index}`}><strong>{check.status === 'READY' ? '호환됨' : '검증 차단'}</strong><span>{pairIdentity(check)}</span>{check.reason_code ? <small>{reasonLabel(check.reason_code)}</small> : null}</li>)}{impact.validation.unverified?.map((item, index) => <li key={`${checkLabel(item)}:${index}`}><strong>검증 안 됨</strong><span>{reasonLabel(item.reason_code)}</span></li>)}</ul>{impact.validation.sample_coverage ? <small>검증 범위: 저장된 레시피 샘플 · {impact.validation.sample_coverage.validated_pair_count}개 조합</small> : null}</div>
          <p className="dialog-help"><AlertTriangle /> 이 조회는 저장된 정의·샘플·참조만 검사하며 파일 전체를 스캔하지 않습니다. 서버가 확인한 동일 버전으로만 활성화합니다.</p>
        </div>
        <div className="dialog-actions semantic-impact-actions"><button type="button" className="ghost-button" onClick={onClose}>취소</button><button type="button" className="primary-button" onClick={onConfirm} disabled={!impact.activation_allowed || status !== 'READY' || confirming}>{confirming ? <LoaderCircle className="spin" /> : <CheckCircle2 />} {confirming ? '활성화 중' : '검토 후 활성화'}</button></div>
      </> : <div className="semantic-impact-body"><p className="semantic-review-item-error" role="alert">영향 미리보기를 불러오지 못했습니다.</p></div>}
    </section>
  </div>
}
