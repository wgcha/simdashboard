import { forwardRef, type ComponentPropsWithoutRef } from 'react'
import './Button.css'

export type ControlSize = 'sm' | 'md' | 'lg'
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export type ButtonProps = ComponentPropsWithoutRef<'button'> & {
  size?: ControlSize
  variant?: ButtonVariant
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, size = 'md', type = 'button', variant = 'secondary', ...props },
  ref,
) {
  const classes = ['shared-button', `shared-button--${size}`, `shared-button--${variant}`, className]
    .filter(Boolean)
    .join(' ')

  return <button ref={ref} className={classes} type={type} {...props} />
})

Button.displayName = 'Button'
