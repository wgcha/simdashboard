import { lazy, Suspense, useState } from 'react'
import { FileText } from 'lucide-react'
import type { CaseReportScope } from './caseReport'
import './CaseReport.css'

const CaseReportDialog = lazy(() => import('./CaseReportDialog').then(({ CaseReportDialog: Dialog }) => ({ default: Dialog })))

/** "보고서" header button. The dialog keeps the selection it was opened with. */
export function CaseReportLauncher({ scope, disabledReason }: { scope: CaseReportScope | null; disabledReason: string }) {
  const [opened, setOpened] = useState<CaseReportScope | null>(null)
  return <>
    <button type="button" className="case-report__trigger" disabled={!scope} title={scope ? '선택한 Case 결과로 보고서를 만듭니다.' : disabledReason} onClick={() => { if (scope) setOpened(structuredClone(scope)) }}>
      <FileText size={15} aria-hidden="true" />보고서
    </button>
    {opened ? <Suspense fallback={null}><CaseReportDialog scope={opened} onClose={() => setOpened(null)} /></Suspense> : null}
  </>
}
