import { useEffect, useRef, useState } from 'react'
import { Download, LoaderCircle, RefreshCw } from 'lucide-react'
import { loadLocalHelperDistribution, type LocalHelperDistribution } from '../../shared/api/localHelperDistribution'
import { downloadLocalHelperSetup, resolveHelperSetupAddress } from './setupLauncher'

export function LocalHelperInstallCard({ busy = false }: { busy?: boolean }) {
  const [distribution, setDistribution] = useState<LocalHelperDistribution | null>(null)
  const [autoStart, setAutoStart] = useState(true)
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const downloading = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setDistribution(null)
    void loadLocalHelperDistribution(controller.signal)
      .then((value) => { if (!controller.signal.aborted) setDistribution(value) })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '설치 파일 정보를 확인하지 못했습니다.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [revision])

  const download = () => {
    if (downloading.current || distribution?.status !== 'ready') return
    downloading.current = true
    setError(''); setNotice('')
    try {
      downloadLocalHelperSetup({ ...resolveHelperSetupAddress(), autoStart, distribution })
      setNotice('설치 파일을 받았습니다. 다운로드한 파일을 실행한 뒤 이 화면에서 다시 확인하세요.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '설치 파일을 준비하지 못했습니다.') }
    finally { downloading.current = false }
  }

  return <section className="local-pc-setup-card" aria-labelledby="local-pc-setup-heading">
    <div className="local-pc-card-heading"><span className="local-pc-eyebrow"><Download aria-hidden="true" /> FIRST-TIME SETUP</span><h2 id="local-pc-setup-heading">PC 도우미 설치</h2><p>이 PC에서 프로그램을 찾고 실행할 도우미를 설치하세요. 필요한 실행 환경이 함께 설치됩니다.</p></div>
    <ol className="local-pc-install-steps"><li>설치 파일을 받아 실행합니다.</li><li>설치가 끝나면 이 화면에서 <strong>다시 확인</strong>을 누릅니다.</li><li><strong>이 PC 연결</strong>을 누르고 PC 승인창을 확인합니다.</li></ol>
    <div className="local-pc-setup-actions"><label className="local-pc-check"><input type="checkbox" checked={autoStart} onChange={(event) => setAutoStart(event.target.checked)} disabled={busy} /><span>Windows 로그인 시 자동 시작</span></label><button type="button" className="local-pc-primary" onClick={download} disabled={busy || loading || distribution?.status !== 'ready'}>{loading ? <LoaderCircle className="local-pc-spin" aria-hidden="true" /> : <Download aria-hidden="true" />} PC 도우미 설치 파일 받기</button></div>
    {loading ? <p className="local-pc-setup-note" role="status">설치 파일을 확인하고 있습니다.</p> : distribution?.status === 'ready' ? <p className="local-pc-setup-note">Windows 64비트 · 버전 {distribution.version} · {(distribution.size_bytes / 1024 / 1024).toFixed(1)} MB · 현재 Windows 사용자에게 설치됩니다.</p> : <div className="local-pc-install-unavailable" role="status"><p>{distribution?.status === 'unavailable' ? distribution.reason : error}</p><p>서버 관리자가 도우미 배포 파일을 준비하면 설치할 수 있습니다.</p><button type="button" className="local-pc-secondary" onClick={() => setRevision((value) => value + 1)}><RefreshCw aria-hidden="true" /> 설치 파일 다시 확인</button></div>}
    {error && distribution?.status === 'ready' && <p className="local-pc-alert" role="alert">{error}</p>}
    {notice && <p className="local-pc-setup-note" role="status">{notice}</p>}
  </section>
}
