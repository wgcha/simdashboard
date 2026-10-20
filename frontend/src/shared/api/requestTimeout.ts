// A request deadline that composes with the caller's AbortSignal (no AbortSignal.any / .timeout needed).
// Pure and dependency-free so the node self-test can load it.

export type TimeoutGuard = { signal: AbortSignal; timedOut: () => boolean; dispose: () => void }

export function timeoutGuard(parent: AbortSignal | undefined, ms: number): TimeoutGuard {
  const controller = new AbortController()
  let expired = false
  const onParentAbort = () => controller.abort()
  if (parent?.aborted) controller.abort()
  else parent?.addEventListener('abort', onParentAbort, { once: true })
  const timer = setTimeout(() => { expired = true; controller.abort() }, ms)
  return {
    signal: controller.signal,
    timedOut: () => expired && !parent?.aborted,
    dispose: () => { clearTimeout(timer); parent?.removeEventListener('abort', onParentAbort) },
  }
}

export const timeoutMessage = (ms: number, what: string) =>
  `${what} 응답이 ${Math.round(ms / 1000)}초 안에 오지 않아 중단했습니다. 드라이브가 느리거나 연결이 끊겼을 수 있습니다. 다시 시도하세요.`
