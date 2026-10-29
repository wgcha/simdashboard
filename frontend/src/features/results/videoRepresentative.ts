/** Pure helpers of the representative-video screens (issue #55): labels, keyword rule parsing, tile model. */
import type { DashboardRunVideo, DashboardVideoSource } from '../../shared/api/simulationDashboard'
import type { VideoGridItem } from './DropVideoParts'

export const MAX_RULE_KEYWORDS = 20
export const MAX_KEYWORD_LENGTH = 64

/** A grid tile: the common video item plus the server row it came from. */
export type VideoTile = VideoGridItem & { video: DashboardRunVideo }

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function sourceLabel(source: DashboardVideoSource | null | undefined): string {
  return source === 'USER' ? '사용자 지정' : source === 'RULE' ? '규칙 선택' : '기본(파일 이름순 첫 번째)'
}

export function sourceShort(source: DashboardVideoSource | null | undefined): string {
  return source === 'USER' ? '사용자 지정' : source === 'RULE' ? '규칙' : '기본'
}

/** A stored video the browser cannot play (codec) or a listed-only file (format / too large). */
export function isUnplayable(video: Pick<DashboardRunVideo, 'asset_id' | 'playable' | 'status'>): boolean {
  return !video.asset_id || video.playable === false || video.status === 'UNPLAYABLE'
}

/** 「재생 불가 · avi · 5.2 MB · 원본 드라이브에 있음」 / 「재생 불가 · 코덱 · mp4v」. */
export function unplayableText(video: Pick<DashboardRunVideo, 'asset_id' | 'unplayable_reason' | 'format' | 'codec' | 'size_bytes' | 'file_name'>): string {
  const size = formatBytes(video.size_bytes)
  const extension = video.format || (video.file_name?.includes('.') ? video.file_name.split('.').pop() : '') || ''
  if (video.unplayable_reason === 'CODEC') return ['재생 불가', '코덱', video.codec || ''].filter(Boolean).join(' · ')
  if (video.unplayable_reason === 'TOO_LARGE') return ['재생 불가', '용량 초과', extension, size, '원본 드라이브에 있음'].filter(Boolean).join(' · ')
  return ['재생 불가', extension, size, '원본 드라이브에 있음'].filter(Boolean).join(' · ')
}

export function toTile(video: DashboardRunVideo, representativeMode: boolean, assetUrl: (assetId: string) => string): VideoTile {
  const name = video.file_name || video.title
  return {
    id: video.video_id,
    title: representativeMode && video.scene_sequence_label ? `${video.scene_sequence_label} ${video.scene_label}` : video.scene_label,
    subtitle: name,
    src: video.asset_id ? assetUrl(video.asset_id) : '',
    status: video.status,
    video,
  }
}

export function parseKeywords(text: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const part of text.split(/[,\n;]/)) {
    const word = part.split(/\s+/).filter(Boolean).join(' ')
    if (!word || seen.has(word.toLowerCase())) continue
    seen.add(word.toLowerCase())
    result.push(word)
  }
  return result
}

/** Why the text cannot be saved as a rule (empty = fine). */
export function keywordProblem(text: string): string {
  const words = parseKeywords(text)
  if (words.length > MAX_RULE_KEYWORDS) return `키워드는 최대 ${MAX_RULE_KEYWORDS}개입니다.`
  const bad = words.find((word) => word.length > MAX_KEYWORD_LENGTH || /[\\/]/.test(word))
  return bad ? `키워드는 ${MAX_KEYWORD_LENGTH}자 이하이며 경로 문자(/ \\)를 쓸 수 없습니다: ${bad.slice(0, 20)}` : ''
}

export function changedAtText(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  const two = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`
}

/** Same Scene across Cases is matched by its folder name (case-insensitive), like the compare table. */
export function sceneMatchKey(label: string): string { return label.trim().toLowerCase() }

export type SceneOption = { key: string; label: string; sequence: number | null }
/** Union of the Scenes of several Cases, ordered by sequence number then name. */
export function unionScenes(lists: Array<Array<{ label: string; scene_sequence_number?: number | null }>>): SceneOption[] {
  const found = new Map<string, SceneOption>()
  for (const list of lists) for (const scene of list) {
    const key = sceneMatchKey(scene.label)
    if (!found.has(key)) found.set(key, { key, label: scene.label, sequence: scene.scene_sequence_number ?? null })
  }
  return [...found.values()].sort((a, b) => Number(a.sequence == null) - Number(b.sequence == null) || (a.sequence ?? 0) - (b.sequence ?? 0) || a.key.localeCompare(b.key))
}
