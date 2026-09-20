import { forwardRef, type ComponentPropsWithoutRef } from 'react'
import type { ControlSize } from './Button'
import './Input.css'

export type InputProps = ComponentPropsWithoutRef<'input'> & {
  controlSize?: ControlSize
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, controlSize = 'md', ...props },
  ref,
) {
  const classes = ['shared-input', `shared-input--${controlSize}`, className].filter(Boolean).join(' ')
  return <input ref={ref} className={classes} {...props} />
})

Input.displayName = 'Input'
