import type { FullResult, Reporter } from '@playwright/test/reporter'

/**
 * Playwright 1.62 can retain a Windows browser/transport handle after all
 * tests and reporters have finished. The outer E2E runner owns and terminates
 * the complete server/browser process trees, so exiting here is deterministic
 * and still preserves the proper success/failure code.
 */
export default class WindowsExitReporter implements Reporter {
  onEnd(result: FullResult) {
    const code = result.status === 'passed' ? 0 : 1
    setTimeout(() => process.exit(code), 100)
  }
}
