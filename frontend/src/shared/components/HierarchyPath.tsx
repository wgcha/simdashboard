import { Children, Fragment, isValidElement, type ReactNode } from 'react'
import './HierarchyPath.css'

type Props = {
  /** Accessible name of the path group. */
  label: string
  children: ReactNode
  /** Content after the path (for example Scene chips); not separated by `›`. */
  trailing?: ReactNode
  className?: string
}

/**
 * One-row Case hierarchy path (Case › 하중경우 › Run Case › Run Option …)
 * shared by the Case results and materials tabs, so both look the same.
 */
export function HierarchyPath({ label, children, trailing, className }: Props) {
  const items = Children.toArray(children).filter(isValidElement)
  return <div className={['shared-hierarchy-path', className].filter(Boolean).join(' ')} role="group" aria-label={label}>
    {items.map((item, index) => <Fragment key={item.key ?? index}>
      {index ? <span className="shared-hierarchy-path__sep" aria-hidden="true">›</span> : null}
      {item}
    </Fragment>)}
    {trailing ? <div className="shared-hierarchy-path__trailing">{trailing}</div> : null}
  </div>
}
