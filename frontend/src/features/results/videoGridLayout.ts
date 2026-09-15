export const DEFAULT_VIDEO_COLUMNS = 2
export const DEFAULT_VIDEO_ROWS = 2
export const MAX_VIDEO_CELLS = 20

export function normalizeVideoGridLayout(columns: unknown, rows: unknown) {
  const normalize = (value: unknown, fallback: number) => {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed >= 1 ? Math.min(MAX_VIDEO_CELLS, Math.floor(parsed)) : fallback
  }
  const safeColumns = normalize(columns, DEFAULT_VIDEO_COLUMNS)
  const safeRows = Math.min(normalize(rows, DEFAULT_VIDEO_ROWS), Math.floor(MAX_VIDEO_CELLS / safeColumns))
  return { columns: safeColumns, rows: safeRows, pageSize: safeColumns * safeRows }
}
