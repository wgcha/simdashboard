import { apiClient, unwrapGenerated } from './client'
import { apiUrl } from './url'

export const LOCAL_HELPER_DOWNLOAD_PATH = apiUrl('/api/local-helper/distribution/download')

export type LocalHelperDistributionReady = {
  status: 'ready'
  version: string
  filename: string
  artifact_url: string
  sha256: string
  size_bytes: number
  released_at: string
}

export type LocalHelperDistribution = LocalHelperDistributionReady | { status: 'unavailable'; reason: string }

export async function loadLocalHelperDistribution(signal?: AbortSignal): Promise<LocalHelperDistribution> {
  const result = unwrapGenerated(await apiClient.GET('/api/local-helper/distribution', { signal }))
  if (!result || typeof result !== 'object') throw new Error('도우미 설치 파일 정보를 확인하지 못했습니다.')
  const value = result as Record<string, unknown>
  if (value.status === 'unavailable' && typeof value.reason === 'string') return { status: 'unavailable', reason: value.reason }
  if (value.status !== 'ready' || typeof value.version !== 'string' || typeof value.filename !== 'string'
    || value.artifact_url !== LOCAL_HELPER_DOWNLOAD_PATH
    || typeof value.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(value.sha256)
    || typeof value.size_bytes !== 'number' || !Number.isSafeInteger(value.size_bytes) || value.size_bytes <= 0
    || typeof value.released_at !== 'string') throw new Error('도우미 설치 파일 정보가 올바르지 않습니다.')
  return value as LocalHelperDistributionReady
}
