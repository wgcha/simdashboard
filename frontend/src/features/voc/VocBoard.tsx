import { useEffect, useRef, useState } from 'react'
import { Download, LoaderCircle, MessageSquare, RefreshCw, Send } from 'lucide-react'
import { vocApi, type VocPost } from '../../shared/api/voc'
import type { AuthUser } from '../../auth'
import './voc.css'

const PAGE_SIZE = 50
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '요청을 처리하지 못했습니다. 다시 시도하세요.'

export function VocBoard({ user }: { user: AuthUser }) {
  const [posts, setPosts] = useState<VocPost[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [notice, setNotice] = useState('')
  const request = useRef<AbortController | null>(null)
  const saveLock = useRef(false)

  const load = async (nextOffset: number) => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true)
    setListError('')
    const deadline = setTimeout(() => controller.abort(), 15_000)
    try {
      const page = await vocApi.list(PAGE_SIZE, nextOffset, controller.signal)
      if (request.current !== controller) return
      setPosts(page.items)
      setTotal(page.total)
      setOffset(page.offset)
    } catch (reason) {
      if (request.current === controller) setListError(controller.signal.aborted ? '목록 조회 시간이 초과되었습니다. 다시 시도하세요.' : errorMessage(reason))
    } finally {
      clearTimeout(deadline)
      if (request.current === controller) setLoading(false)
    }
  }
  useEffect(() => {
    void load(0)
    return () => { request.current?.abort(); request.current = null }
  }, [])

  const submit = async () => {
    const content = draft.trim()
    if (!content || content.length > 10_000 || saveLock.current) return
    saveLock.current = true
    setSaving(true)
    setError('')
    setNotice('')
    try {
      await vocApi.create(content)
      setDraft('')
      setNotice('의견을 등록했습니다.')
      await load(0)
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      saveLock.current = false
      setSaving(false)
    }
  }
  const download = async (format: 'csv' | 'json') => {
    if (!user.is_global_admin || exporting) return
    setExporting(true)
    setError('')
    try { await vocApi.download(format) }
    catch (reason) { setError(errorMessage(reason)) }
    finally { setExporting(false) }
  }

  return <section className="voc-board" data-testid="voc-board">
    <header className="voc-header">
      <div><span className="voc-eyebrow"><MessageSquare /> VOC BOARD</span><h1>VOC 게시판</h1><p>서비스 사용 중 느낀 점과 개선 아이디어를 회원들과 공유해 주세요.</p></div>
      {user.is_global_admin ? <div className="voc-downloads">
        <button type="button" disabled={exporting} onClick={() => void download('csv')}><Download /> CSV 다운로드</button>
        <button type="button" disabled={exporting} onClick={() => void download('json')}><Download /> JSON 다운로드</button>
      </div> : null}
    </header>
    <section className="voc-compose">
      <p className="voc-author">작성자: <strong>{user.display_name}</strong> ({user.username}) · 회원정보 자동 기록</p>
      <label htmlFor="voc-content">개선 의견</label>
      <textarea id="voc-content" maxLength={10000} disabled={saving} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="개선이 필요한 점이나 아이디어를 입력해 주세요." />
      <div><span>{draft.length.toLocaleString()} / 10,000</span><button type="button" onClick={() => void submit()} disabled={saving || !draft.trim()}>{saving ? <LoaderCircle className="spin" /> : <Send />} 등록</button></div>
    </section>
    {notice && <p className="voc-notice" role="status">{notice}</p>}
    {error && <p className="voc-error" role="alert">{error}</p>}
    <div className="voc-list-heading"><h2>등록된 의견 <span>{total.toLocaleString()}건</span></h2><button type="button" disabled={loading || saving} onClick={() => void load(offset)}><RefreshCw /> 새로고침</button></div>
    {listError && <div className="voc-error" role="alert">{listError}<button type="button" disabled={loading} onClick={() => void load(offset)}>다시 시도</button></div>}
    <section className="voc-list" aria-live="polite" aria-busy={loading}>
      {loading ? <p className="voc-empty"><LoaderCircle className="spin" /> 의견을 불러오는 중입니다.</p> : posts.length ? posts.map((post) => <article key={post.id}>
        <header><strong>{post.author_display_name} <small>({post.author_username})</small></strong><time dateTime={post.created_at}>{new Date(post.created_at).toLocaleString('ko-KR')}</time></header>
        <p>{post.content}</p>
      </article>) : !listError && <p className="voc-empty">등록된 개선 의견이 없습니다. 첫 의견을 남겨 주세요.</p>}
    </section>
    {total > PAGE_SIZE && <nav className="voc-pagination" aria-label="VOC 페이지 이동">
      <button type="button" disabled={offset === 0 || loading || saving} onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}>이전</button>
      <span>{Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(total / PAGE_SIZE)}</span>
      <button type="button" disabled={offset + PAGE_SIZE >= total || loading || saving} onClick={() => void load(offset + PAGE_SIZE)}>다음</button>
    </nav>}
  </section>
}
