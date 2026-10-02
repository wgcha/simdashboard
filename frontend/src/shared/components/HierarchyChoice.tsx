import { Select } from './Select'
import './HierarchyChoice.css'

export type HierarchyChoiceOption = {
  id: string
  label: string
  /** Full text for the tooltip, for example a relative folder path. */
  title?: string | null
  disabled?: boolean
  reason?: string | null
}

type Props = {
  label: string
  value: string
  choices: readonly HierarchyChoiceOption[]
  onChange: (value: string) => void
  disabled?: boolean
  /** Tooltip shown when the level cannot be chosen (no parent selection or no candidates). */
  disabledReason?: string
  /** Placeholder text for a level without candidates. */
  emptyText?: string
  className?: string
}

/**
 * One level of the Case hierarchy path. A single candidate is shown as fixed
 * text (the caller auto-selects it); several candidates become a labelled select.
 */
export function HierarchyChoice({ label, value, choices, onChange, disabled = false, disabledReason, emptyText = '없음', className }: Props) {
  const unique = Array.from(new Map(choices.map((choice) => [choice.id, choice])).values())
  const classes = ['shared-hierarchy-choice', className].filter(Boolean).join(' ')
  if (!disabled && unique.length === 1) {
    const only = unique[0]
    return <div className={`${classes} shared-hierarchy-choice--fixed`} data-hierarchy-level={label}>
      <span>{label}</span>
      <b title={only.title ?? only.label}>{only.label}</b>
    </div>
  }
  const unavailable = disabled || !unique.length
  return <label className={classes} title={unavailable ? disabledReason : undefined} data-hierarchy-level={label}>
    <span>{label}</span>
    <Select controlSize="sm" aria-label={label} value={unavailable ? '' : value} disabled={unavailable} onChange={(event) => onChange(event.target.value)}>
      {unavailable
        ? <option value="">{emptyText}</option>
        : <>
          <option value="">선택</option>
          {unique.map((choice) => <option key={choice.id} value={choice.id} disabled={choice.disabled} title={choice.reason ?? choice.title ?? undefined}>{choice.label}</option>)}
        </>}
    </Select>
  </label>
}
