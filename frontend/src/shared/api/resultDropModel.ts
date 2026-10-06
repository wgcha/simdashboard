// Pure helpers for the W8 결과 등록 (drag & drop) screen; covered by scripts/shared-api-self-test.mjs.

export type DropRole = 'WORKING' | 'SIMULATION_CASE' | 'LOAD_CASE' | 'EXECUTION_RUN' | 'RUN_OPTION' | 'SCENE' | 'CONTAINER' | 'CONTENT'

export type PickedFile = { relativePath: string; file: File }
export type PickedItems = { files: PickedFile[]; folders: string[] }

/** Minimal FileSystemEntry shape (webkitGetAsEntry); duck-typed so tests can pass plain objects. */
export type EntryLike = {
  isFile: boolean
  isDirectory: boolean
  name: string
  fullPath: string
  file?: (success: (file: File) => void, failure?: (error: unknown) => void) => void
  createReader?: () => { readEntries: (success: (entries: EntryLike[]) => void, failure?: (error: unknown) => void) => void }
}

export const ROLE_LABELS: Record<string, string> = {
  WORKING: 'Working', SIMULATION_CASE: 'Case', LOAD_CASE: '하중경우', EXECUTION_RUN: 'Run Case',
  RUN_OPTION: 'Run Option', SCENE: 'Scene', CONTAINER: '폴더', CONTENT: 'Scene 안',
}

export const SKIP_REASON_LABELS: Record<string, string> = {
  BLOCKED_EXTENSION: '실행 파일·스크립트', SYSTEM_FILE: '시스템·임시 파일', HIDDEN_FOLDER: '숨김 폴더 안',
}

function trimSlashes(value: string) { return value.replace(/^\/+/, '').replace(/\/+$/, '') }

/** Relative path of an entry from its ``fullPath`` ("/Folder/a.csv" → "Folder/a.csv"). */
export function entryRelativePath(entry: Pick<EntryLike, 'fullPath' | 'name'>) {
  return trimSlashes(entry.fullPath || entry.name)
}

function readAll(reader: ReturnType<NonNullable<EntryLike['createReader']>>): Promise<EntryLike[]> {
  // readEntries returns batches (Chrome: 100) until an empty batch.
  return new Promise((resolve, reject) => {
    const all: EntryLike[] = []
    const next = () => reader.readEntries((batch) => {
      if (!batch.length) { resolve(all); return }
      all.push(...batch)
      next()
    }, reject)
    next()
  })
}

function entryFile(entry: EntryLike): Promise<File> {
  return new Promise((resolve, reject) => {
    if (!entry.file) { reject(new Error('파일을 읽을 수 없습니다.')); return }
    entry.file(resolve, reject)
  })
}

/** Walk dropped entries (files and whole folders), keeping the relative structure and empty folders. */
export async function walkEntries(entries: EntryLike[]): Promise<PickedItems> {
  const files: PickedFile[] = []
  const folders: string[] = []
  const visit = async (entry: EntryLike): Promise<void> => {
    if (entry.isFile) {
      files.push({ relativePath: entryRelativePath(entry), file: await entryFile(entry) })
      return
    }
    if (!entry.isDirectory || !entry.createReader) return
    const children = await readAll(entry.createReader())
    if (!children.length) folders.push(entryRelativePath(entry))
    for (const child of children) await visit(child)
  }
  for (const entry of entries) await visit(entry)
  return { files, folders }
}

/** Files from a file input; ``webkitdirectory`` inputs carry the folder in ``webkitRelativePath``. */
export function pickedFromInput(list: ArrayLike<File>): PickedItems {
  const files = Array.from(list).map((file) => ({
    relativePath: trimSlashes((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name),
    file,
  }))
  return { files, folders: [] }
}

/**
 * Entries of a drop event. ``webkitGetAsEntry`` must be called synchronously in the
 * handler (the DataTransfer is cleared afterwards); falls back to the plain file list.
 */
export function dropEntries(transfer: { items?: ArrayLike<{ kind: string; webkitGetAsEntry?: () => EntryLike | null }>; files?: ArrayLike<File> } | null) {
  const entries: EntryLike[] = []
  for (const item of Array.from(transfer?.items ?? [])) {
    if (item.kind !== 'file') continue
    const entry = item.webkitGetAsEntry?.()
    if (entry) entries.push(entry)
  }
  return { entries, files: entries.length ? [] : Array.from(transfer?.files ?? []) }
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`
}

/** What belongs directly inside a folder of ``role`` (the guide text for the selected location). */
export function dropGuide(role: string, roles: readonly string[]): { put: string; example: string } {
  const index = roles.indexOf(role)
  const child = index >= 0 ? roles[index + 1] : undefined
  if (role === 'SCENE' || role === 'CONTENT' || !child) {
    return { put: '결과 파일(CSV·영상·입력 파일 등)을 그대로 넣으세요.', example: 'MAX_RESULT_…csv, scene.mp4' }
  }
  const label = ROLE_LABELS[child] ?? child
  const examples: Record<string, string> = {
    SIMULATION_CASE: 'Package_Model_SetCase1…', LOAD_CASE: 'Drop', EXECUTION_RUN: '85qn80h_ref…',
    RUN_OPTION: 'INDIVIDUAL, CUMULATIVE', SCENE: '1_Face, 2_Face …',
  }
  return { put: `${label} 폴더를 통째로 넣으세요.`, example: examples[child] ?? '' }
}

/** Rule lines "X 폴더를 통째로 복사하려면 Y 폴더 아래에" for every level below Working. */
export function depthRules(roles: readonly string[]) {
  const rules: Array<{ role: string; parent: string; text: string }> = []
  for (let index = 1; index < roles.length; index += 1) {
    const role = roles[index]
    const parent = roles[index - 1]
    rules.push({ role, parent, text: `${ROLE_LABELS[role] ?? role} 폴더를 통째로 복사하려면 ${ROLE_LABELS[parent] ?? parent} 폴더 아래에` })
  }
  return rules
}

/** Ordered chunk ranges for a file of ``size`` bytes, starting at ``offset``. */
export function chunkRanges(size: number, chunk: number, offset = 0) {
  const ranges: Array<[number, number]> = []
  if (size === 0) return offset === 0 ? [[0, 0] as [number, number]] : ranges
  for (let start = offset; start < size; start += chunk) ranges.push([start, Math.min(size, start + chunk)])
  return ranges
}
