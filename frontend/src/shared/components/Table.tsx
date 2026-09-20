import { forwardRef, type ComponentPropsWithoutRef } from 'react'
import './Table.css'

function withClass(baseClass: string, className?: string) {
  return [baseClass, className].filter(Boolean).join(' ')
}

export const TableScroll = forwardRef<HTMLDivElement, ComponentPropsWithoutRef<'div'>>(
  function TableScroll({ className, ...props }, ref) {
    return <div ref={ref} className={withClass('shared-table-scroll', className)} {...props} />
  },
)

export const Table = forwardRef<HTMLTableElement, ComponentPropsWithoutRef<'table'>>(
  function Table({ className, ...props }, ref) {
    return <table ref={ref} className={withClass('shared-table', className)} {...props} />
  },
)

export const TableHead = forwardRef<HTMLTableSectionElement, ComponentPropsWithoutRef<'thead'>>(
  function TableHead({ className, ...props }, ref) {
    return <thead ref={ref} className={withClass('shared-table-head', className)} {...props} />
  },
)

export const TableBody = forwardRef<HTMLTableSectionElement, ComponentPropsWithoutRef<'tbody'>>(
  function TableBody({ className, ...props }, ref) {
    return <tbody ref={ref} className={withClass('shared-table-body', className)} {...props} />
  },
)

export const TableRow = forwardRef<HTMLTableRowElement, ComponentPropsWithoutRef<'tr'>>(
  function TableRow({ className, ...props }, ref) {
    return <tr ref={ref} className={withClass('shared-table-row', className)} {...props} />
  },
)

export const TableHeaderCell = forwardRef<HTMLTableCellElement, ComponentPropsWithoutRef<'th'>>(
  function TableHeaderCell({ className, ...props }, ref) {
    return <th ref={ref} className={withClass('shared-table-header-cell', className)} {...props} />
  },
)

export const TableCell = forwardRef<HTMLTableCellElement, ComponentPropsWithoutRef<'td'>>(
  function TableCell({ className, ...props }, ref) {
    return <td ref={ref} className={withClass('shared-table-cell', className)} {...props} />
  },
)

TableScroll.displayName = 'TableScroll'
Table.displayName = 'Table'
TableHead.displayName = 'TableHead'
TableBody.displayName = 'TableBody'
TableRow.displayName = 'TableRow'
TableHeaderCell.displayName = 'TableHeaderCell'
TableCell.displayName = 'TableCell'
