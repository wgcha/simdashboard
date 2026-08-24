import { AlertTriangle, CheckCircle2, FolderSync, LoaderCircle } from 'lucide-react'
import { useState } from 'react'

import { refreshMasterResultFolder, type MasterResultRefreshResponse } from '../../shared/api/masterResultRefresh'
import './MasterResultFolderRefresh.css'

export function MasterResultFolderRefresh() {
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<MasterResultRefreshResponse | null>(null)

  const refresh = async () => {
    setRefreshing(true)
    setError('')
    try {
      setResult(await refreshMasterResultFolder())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '마스터 결과 폴더를 갱신하지 못했습니다.')
    } finally {
      setRefreshing(false)
    }
  }

  return <section className="master-result-refresh" aria-labelledby="master-result-refresh-heading">
    <header>
      <div><span>SERVER RESULT INGESTION</span><h2 id="master-result-refresh-heading">마스터 결과 폴더</h2><p>서버에 지정된 폴더에서 의뢰 결과를 탐색하고 검증된 신규 Run을 DB에 반영합니다.</p></div>
      <button type="button" onClick={() => void refresh()} disabled={refreshing}>{refreshing ? <LoaderCircle className="spin" aria-hidden="true" /> : <FolderSync aria-hidden="true" />} 마스터 폴더 Refresh</button>
    </header>
    {error ? <p className="master-result-refresh-error" role="alert"><AlertTriangle aria-hidden="true" />{error}</p> : null}
    {result ? <div className="master-result-refresh-result">
      <div className="master-result-refresh-counts"><span><strong>{result.scanned_count}</strong> 탐색</span><span><strong>{result.imported_count}</strong> 등록</span><span><strong>{result.skipped_count}</strong> 중복 제외</span><span className={result.failed_count ? 'failed' : ''}><strong>{result.failed_count}</strong> 실패</span></div>
      <div className="master-result-refresh-items">{result.items.length ? result.items.map((item) => <article key={item.manifest_path}><i>{item.status === 'FAILED' ? <AlertTriangle aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}</i><span><strong>{item.manifest_path}</strong><small>{item.message || item.analysis_run_id || item.status}</small></span><b className={item.status.toLowerCase()}>{item.status}</b></article>) : <p>발견된 manifest가 없습니다.</p>}</div>
    </div> : null}
  </section>
}
