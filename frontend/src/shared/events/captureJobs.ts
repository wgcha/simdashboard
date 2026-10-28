/** Window event: ask the administrator's 「결과 받기 상태」 panel to open (optionally filtered). No-op when it is not mounted. */
export const OPEN_CAPTURE_JOBS_EVENT = 'simdash:open-capture-jobs'
export type OpenCaptureJobsDetail = { filter?: 'ALL' | 'RUNNING' | 'PENDING' | 'FAILED' | 'COMPLETED' }
export const openCaptureJobs = (detail: OpenCaptureJobsDetail = {}) => window.dispatchEvent(new CustomEvent<OpenCaptureJobsDetail>(OPEN_CAPTURE_JOBS_EVENT, { detail }))
