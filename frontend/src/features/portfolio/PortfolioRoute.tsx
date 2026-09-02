import { PortfolioDashboard } from '../../PortfolioDashboard'
import type { PortfolioLayout } from '../../types'

type PortfolioRouteProps = {
  editMode: boolean
  layout: PortfolioLayout
  layoutVersion: number
  refreshToken: number
  onCancelEdit: () => void
  onLayoutChange: (layout: PortfolioLayout) => void
  onOpen: (projectId: string, requestId: string) => void
  onResetLayout: () => void
}

/** Route adapter for the company operational dashboard. */
export function PortfolioRoute(props: PortfolioRouteProps) {
  return <PortfolioDashboard {...props} />
}
