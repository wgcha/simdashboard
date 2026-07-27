import type { PortfolioLayout, WorkflowDashboardLayout } from '../../types'

export const DEFAULT_PORTFOLIO_LAYOUT: PortfolioLayout = {
  fontSize: 10,
  chartOrder: ['trend', 'status', 'quality', 'type'],
}

export function loadPortfolioLayout(): PortfolioLayout {
  return { ...DEFAULT_PORTFOLIO_LAYOUT, chartOrder: [...DEFAULT_PORTFOLIO_LAYOUT.chartOrder] }
}

export const DEFAULT_WORKFLOW_DASHBOARD_LAYOUT: WorkflowDashboardLayout = {
  fontSize: 10,
  accentColor: '#50d5ff',
  items: [],
}

export function loadWorkflowDashboardLayout(): WorkflowDashboardLayout {
  return { ...DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, items: [] }
}
