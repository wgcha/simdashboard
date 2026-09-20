import { forwardRef, type ComponentPropsWithoutRef } from 'react'
import type { ControlSize } from './Button'
import './Select.css'

export type SelectProps = ComponentPropsWithoutRef<'select'> & {
  controlSize?: ControlSize
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, controlSize = 'md', ...props },
  ref,
) {
  const classes = ['shared-select', `shared-select--${controlSize}`, className].filter(Boolean).join(' ')
  return <select ref={ref} className={classes} {...props} />
})

Select.displayName = 'Select'
