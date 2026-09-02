import type { DashboardPageSummary, Overview } from '../../types'

export type ActiveView = 'open_cell' | 'chassis' | 'custom' | 'workflow' | 'compare'

export function pageView(page: DashboardPageSummary): ActiveView {
  return page.page.analysis_key === 'open_cell'
    ? 'open_cell'
    : page.page.analysis_key === 'chassis_rear'
      ? 'chassis'
      : page.page.analysis_key === 'run_comparison'
        ? 'compare'
        : 'custom'
}

export function visiblePages(pages: DashboardPageSummary[], overview: Overview) {
  return pages.filter((page) => page.page.analysis_key === 'custom'
    || (page.page.analysis_key === 'open_cell' && overview.analysis_verdicts.open_cell !== 'NO_DATA')
    || (page.page.analysis_key === 'chassis_rear' && overview.analysis_verdicts.chassis_rear !== 'NO_DATA')
    || (page.page.analysis_key === 'run_comparison' && Boolean(overview.run)))
}

export function preferredPage(pages: DashboardPageSummary[], overview: Overview, preferred?: ActiveView) {
  const available = visiblePages(pages, overview)
  const key = preferred === 'chassis'
    ? 'chassis_rear'
    : preferred === 'open_cell'
      ? 'open_cell'
      : preferred === 'compare'
        ? 'run_comparison'
        : preferred === 'custom'
          ? 'custom'
          : null
  return (key ? available.find((page) => page.page.analysis_key === key) : undefined) ?? available[0] ?? pages[0]
}
