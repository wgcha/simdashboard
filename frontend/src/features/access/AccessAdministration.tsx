import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, LoaderCircle, RefreshCw, RotateCcw, Save, Search, ShieldCheck, Users } from 'lucide-react'

import { api } from '../../api'
import type { ProjectRole } from '../../auth'
import type { MenuPolicy } from '../auth/access'

type AdminUser = Awaited<ReturnType<typeof api.adminUsers>>[number]
type ProjectMember = Awaited<ReturnType<typeof api.projectMembers>>[number]
const roleLabels: Record<ProjectRole, string> = { general: '일반 사용자', power: '파워 사용자', admin: '프로젝트 관리자' }

export function AccessAdminPage({ projectId, projects = [], canApproveUsers, onAccessChanged, onProjectChange = () => {} }: { projectId: string; projects?: readonly { id: string; name: string }[]; canApproveUsers: boolean; onAccessChanged: () => Promise<void>; onProjectChange?: (projectId: string) => void }) {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [members, setMembers] = useState<ProjectMember[]>([])
  const [query, setQuery] = useState('')
  const [reason, setReason] = useState('권한 관리 화면에서 변경')
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [addingUserId, setAddingUserId] = useState<string | null>(null)
  const [mutationPending, setMutationPending] = useState(false)
  const [pendingRoles, setPendingRoles] = useState<Record<string, ProjectRole>>({})
  const [roleDrafts, setRoleDrafts] = useState<Record<string, ProjectRole>>({})
  const currentProjectId = useRef(projectId)
  currentProjectId.current = projectId
  const reloadGeneration = useRef(0)
  const reloadController = useRef<AbortController | null>(null)
  const [memberReadOnly, setMemberReadOnly] = useState(true)
  const [userReadOnly, setUserReadOnly] = useState(true)

  const reload = async () => {
    if (currentProjectId.current !== projectId) return
    const generation = ++reloadGeneration.current
    reloadController.current?.abort()
    const controller = new AbortController()
    reloadController.current = controller
    const timeout = window.setTimeout(() => controller.abort(), 15_000)
    setLoading(true)
    setMemberReadOnly(true)
    setUserReadOnly(canApproveUsers)
    try {
      const memberPromise = projectId ? api.projectMembers(projectId, controller.signal) : Promise.resolve([] as ProjectMember[])
      const userPromise = canApproveUsers ? api.adminUsers(new URLSearchParams(query.trim() ? { q: query.trim() } : {}), controller.signal) : Promise.resolve([] as AdminUser[])
      const [memberResult, userResult] = await Promise.allSettled([memberPromise, userPromise])
      if (generation !== reloadGeneration.current || currentProjectId.current !== projectId) return
      const failures: string[] = []
      if (memberResult.status === 'fulfilled') { setMembers(memberResult.value); setMemberReadOnly(false) }
      else failures.push('프로젝트 역할')
      if (userResult.status === 'fulfilled') { setUsers(userResult.value); setUserReadOnly(false) }
      else failures.push('사용자 목록')
      setMessage(controller.signal.aborted ? '권한 정보 조회 시간이 초과되었습니다. 새로고침으로 다시 시도하세요.' : failures.length ? `${failures.join('·')}을 불러오지 못했습니다. 새로고침으로 다시 시도하세요.` : '')
    } catch (error) {
      if (generation === reloadGeneration.current) setMessage(controller.signal.aborted ? '권한 정보 조회 시간이 초과되었습니다. 새로고침으로 다시 시도하세요.' : error instanceof Error ? error.message : '권한 정보를 불러오지 못했습니다.')
    } finally {
      window.clearTimeout(timeout)
      if (generation === reloadGeneration.current) setLoading(false)
    }
  }

  useEffect(() => {
    setMembers([])
    setPendingRoles({})
    setRoleDrafts({})
    void reload()
    return () => { reloadGeneration.current += 1; reloadController.current?.abort() }
  }, [projectId, canApproveUsers])

  const changeStatus = async (user: AdminUser, accountStatus: AdminUser['account_status']) => {
    if (mutationPending) return
    if (userReadOnly) return setMessage('사용자 목록을 먼저 새로고침하세요.')
    if (reason.trim().length < 2) return setMessage('변경 사유를 두 글자 이상 입력하세요.')
    setMutationPending(true)
    try {
      await api.updateUserStatus(user.id, { account_status: accountStatus, expected_updated_at: user.updated_at, reason: reason.trim() })
      await Promise.all([reload(), onAccessChanged()])
    } catch (error) { setMessage(error instanceof Error ? error.message : '계정 상태를 변경하지 못했습니다.') } finally { setMutationPending(false) }
  }

  const toggleGlobalAdmin = async (user: AdminUser) => {
    if (mutationPending) return
    if (userReadOnly) return setMessage('사용자 목록을 먼저 새로고침하세요.')
    if (reason.trim().length < 2) return setMessage('변경 사유를 두 글자 이상 입력하세요.')
    setMutationPending(true)
    try {
      await api.updateGlobalAdmin(user.id, { is_global_admin: !user.is_global_admin, expected_updated_at: user.updated_at, reason: reason.trim() })
      await Promise.all([reload(), onAccessChanged()])
    } catch (error) { setMessage(error instanceof Error ? error.message : '전역 관리자 상태를 변경하지 못했습니다.') } finally { setMutationPending(false) }
  }

  const changeRole = async (member: ProjectMember) => {
    if (mutationPending) return
    if (memberReadOnly || !projectId) return setMessage('프로젝트 역할 목록을 먼저 새로고침하세요.')
    const role = roleDrafts[member.user_id]
    if (!role || role === member.role) return
    setMutationPending(true)
    try {
      await api.updateProjectMember(projectId, member.user_id, role, member.updated_at)
      await Promise.all([reload(), onAccessChanged()])
      if (currentProjectId.current !== projectId) return
      setRoleDrafts((current) => { const next = { ...current }; delete next[member.user_id]; return next })
      setMessage(`${member.display_name}님의 프로젝트 역할을 ${roleLabels[role]}(으)로 변경했습니다.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : '프로젝트 역할을 변경하지 못했습니다.') } finally { setMutationPending(false) }
  }

  const addMember = async (user: AdminUser, role: ProjectRole) => {
    if (mutationPending) return
    if (userReadOnly || memberReadOnly) return setMessage('사용자와 프로젝트 역할 목록을 먼저 새로고침하세요.')
    if (!projectId) return setMessage('프로젝트를 먼저 선택하세요.')
    if (user.account_status !== 'ACTIVE') return setMessage('활성 계정만 프로젝트에 추가할 수 있습니다.')
    setAddingUserId(user.id); setMutationPending(true)
    try {
      await api.createProjectMember(projectId, user.id, role)
      await Promise.all([reload(), onAccessChanged()])
      if (currentProjectId.current !== projectId) return
      setPendingRoles((current) => { const next = { ...current }; delete next[user.id]; return next })
      setMessage(`${user.display_name} 계정을 프로젝트에 추가했습니다.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '프로젝트 멤버를 추가하지 못했습니다.')
    } finally {
      setAddingUserId(null); setMutationPending(false)
    }
  }

  return <section className="access-admin-page">
    <header className="content-head"><div><div className="eyebrow"><span>ACCESS CONTROL</span></div><h1>사용자·프로젝트 권한</h1><p>계정 상태와 프로젝트 역할을 서로 독립적으로 관리합니다.</p><label><span>관리할 프로젝트</span><select aria-label="관리할 프로젝트" value={projectId} onChange={(event) => onProjectChange(event.target.value)} disabled={mutationPending || !projects.length}><option value="">프로젝트 선택</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label></div><button className="ghost-button" onClick={() => void reload()} disabled={loading || mutationPending}><RefreshCw className={loading ? 'spin' : ''} /> 새로고침</button></header>
    {!projectId && <p role="status">관리할 프로젝트를 선택하면 프로젝트별 역할을 확인하고 배정할 수 있습니다.</p>}
    {message ? <div className="edit-banner" role="alert"><AlertTriangle /> {message}</div> : null}
    {canApproveUsers ? <section className="access-admin-card"><header><div><span>GLOBAL USERS</span><h2>계정 승인·전역 관리자·프로젝트 배정 확인</h2><p>미배정 사용자는 역할을 고른 뒤 확인하면 프로젝트에 추가됩니다. 이미 배정된 사용자는 아래 역할 목록에서 변경 후 확인하세요.</p></div></header><div className="access-admin-toolbar"><label><Search /><input aria-label="사용자 검색" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이름·아이디·임직원 ID" /></label><label><span>변경 사유</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label><button disabled={mutationPending} onClick={() => void reload()}>검색</button></div><div className="access-admin-table">{users.map((user) => { const member = members.find((item) => item.user_id === user.id); const selectedRole = pendingRoles[user.id] ?? ''; return <article key={user.id}><div><strong>{user.display_name}</strong><span>{user.username} · {user.employee_id || '임직원 ID 없음'}</span></div><b>{user.is_global_admin ? '전역 관리자' : user.account_status}</b><div className="access-admin-actions"><button disabled={mutationPending || userReadOnly || user.account_status === 'ACTIVE'} onClick={() => void changeStatus(user, 'ACTIVE')}><Check /> 승인</button><button disabled={mutationPending || userReadOnly || user.account_status === 'SUSPENDED'} onClick={() => void changeStatus(user, 'SUSPENDED')}>중지</button><button disabled={mutationPending || userReadOnly} onClick={() => void toggleGlobalAdmin(user)}><ShieldCheck /> {user.is_global_admin ? '전역 관리자 해제' : '전역 관리자 지정'}</button>{member ? <span className="access-admin-role-status">{roleLabels[member.role]}</span> : <><select aria-label={`${user.display_name} 프로젝트 역할`} value={selectedRole} disabled={mutationPending || userReadOnly || memberReadOnly || !projectId || user.account_status !== 'ACTIVE' || addingUserId === user.id} onChange={(event) => setPendingRoles((current) => ({ ...current, [user.id]: event.target.value as ProjectRole }))}><option value="">역할 선택</option><option value="general">일반 사용자</option><option value="power">파워 사용자</option><option value="admin">프로젝트 관리자</option></select><button type="button" disabled={mutationPending || userReadOnly || memberReadOnly || !projectId || !selectedRole || user.account_status !== 'ACTIVE' || addingUserId === user.id} onClick={() => void addMember(user, selectedRole as ProjectRole)}>확인</button>{addingUserId === user.id && <LoaderCircle className="spin" aria-label="프로젝트 추가 중" />}</>}</div></article> })}</div></section> : null}
    <section className="access-admin-card"><header><div><span>PROJECT MEMBERS</span><h2>프로젝트 역할 확인</h2><p>역할을 선택해도 저장되지 않습니다. 변경사항을 확인하면 역할이 바뀌고, 취소하면 저장된 역할로 돌아갑니다.</p></div><strong>{members.length}명</strong></header><div className="access-admin-table">{(['general', 'power', 'admin'] as ProjectRole[]).map((role) => { const grouped = members.filter((member) => member.role === role); return <section key={role} aria-label={roleLabels[role]}><header><strong>{roleLabels[role]}</strong><span>{grouped.length}명</span></header>{grouped.length ? grouped.map((member) => { const draftRole = roleDrafts[member.user_id]; const hasRoleChange = Boolean(draftRole && draftRole !== member.role); return <article key={member.user_id}><div><strong>{member.display_name}</strong><span>{member.username} · {member.employee_id || '-'}</span>{hasRoleChange ? <span className="access-admin-role-status" role="status">저장 전: {roleLabels[draftRole!]}</span> : null}</div><div className="access-admin-actions"><label><span className="sr-only">프로젝트 역할</span><select value={draftRole ?? member.role} disabled={mutationPending || memberReadOnly || !projectId} onChange={(event) => setRoleDrafts((current) => ({ ...current, [member.user_id]: event.target.value as ProjectRole }))}><option value="general">일반 사용자</option><option value="power">파워 사용자</option><option value="admin">프로젝트 관리자</option></select></label><button type="button" disabled={mutationPending || memberReadOnly || !projectId || !hasRoleChange} onClick={() => void changeRole(member)}>확인</button><button type="button" disabled={mutationPending || memberReadOnly || !projectId || !draftRole} onClick={() => setRoleDrafts((current) => { const next = { ...current }; delete next[member.user_id]; return next })}>취소</button></div></article> }) : <p>배정된 사용자가 없습니다.</p>}</section> })}</div></section>
  </section>
}

export function MenuPolicyAdminPage({ policy, onPolicyChanged }: { policy: MenuPolicy; onPolicyChanged: (policy: MenuPolicy) => void }) {
  const [draft, setDraft] = useState(policy)
  const [note, setNote] = useState('메뉴 노출 정책 변경')
  const [versions, setVersions] = useState<Awaited<ReturnType<typeof api.menuPolicyVersions>>>([])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => setDraft(policy), [policy])
  useEffect(() => { api.menuPolicyVersions().then(setVersions).catch(() => setVersions([])) }, [policy.version])
  const setVisible = (menuId: string, role: ProjectRole, visible: boolean) => setDraft((current) => ({ ...current, menus: current.menus.map((menu) => menu.id === menuId ? { ...menu, visibility: { ...menu.visibility, [role]: visible } } : menu) }))
  const save = async () => {
    setSaving(true)
    try {
      const visibility = Object.fromEntries((['general', 'power', 'admin'] as ProjectRole[]).map((role) => [role, Object.fromEntries(draft.menus.map((menu) => [menu.id, menu.visibility[role]]))]))
      const updated = await api.updateMenuPolicy({ expected_version: policy.version, change_note: note, visibility })
      onPolicyChanged(updated); setMessage(`정책 v${updated.version}을 저장했습니다.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : '메뉴 정책을 저장하지 못했습니다.') } finally { setSaving(false) }
  }
  const restore = async (version: number) => {
    try { const updated = await api.restoreMenuPolicy(version); onPolicyChanged(updated); setMessage(`v${version}을 새 버전 v${updated.version}으로 복원했습니다.`) } catch (error) { setMessage(error instanceof Error ? error.message : '정책을 복원하지 못했습니다.') }
  }
  return <section className="access-admin-page"><header className="content-head"><div><div className="eyebrow"><span>MENU POLICY · VERSION {policy.version}</span></div><h1>권한 및 좌측 메뉴 정책</h1><p>권한이 있는 기능만 역할별 노출 정책을 추가로 통과할 수 있습니다.</p></div><button className="primary-button" disabled={saving || note.trim().length < 2} onClick={() => void save()}>{saving ? <LoaderCircle className="spin" /> : <Save />} 정책 저장</button></header>{message ? <div className="edit-banner" role="status">{message}</div> : null}<label className="access-policy-note"><span>변경 사유</span><input value={note} onChange={(event) => setNote(event.target.value)} /></label><section className="access-admin-card"><div className="menu-policy-grid header"><strong>메뉴</strong><strong>일반</strong><strong>파워</strong><strong>관리자</strong></div>{draft.menus.map((menu) => <div className="menu-policy-grid" key={menu.id}><span><strong>{menu.label}</strong><small>{menu.required_permission}</small></span>{(['general', 'power', 'admin'] as ProjectRole[]).map((role) => <label key={role}><input type="checkbox" checked={menu.visibility[role]} disabled={!menu.is_policy_editable} onChange={(event) => setVisible(menu.id, role, event.target.checked)} /><span className="sr-only">{menu.label} {role}</span></label>)}</div>)}</section><section className="access-admin-card"><header><div><span>POLICY HISTORY</span><h2>버전 이력</h2></div></header><div className="access-admin-table">{versions.map((version) => <article key={version.version}><div><strong>v{version.version}</strong><span>{version.change_note || '-'} · {version.created_by}</span></div><button disabled={version.version === policy.version} onClick={() => void restore(version.version)}><RotateCcw /> 복원</button></article>)}</div></section></section>
}

export function AuditAdminPage() {
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([])
  const [error, setError] = useState('')
  const load = () => api.auditEvents().then(setEvents).catch((reason) => setError(reason instanceof Error ? reason.message : '감사로그를 불러오지 못했습니다.'))
  useEffect(() => { void load() }, [])
  return <section className="access-admin-page"><header className="content-head"><div><div className="eyebrow"><span>AUDIT TRAIL</span></div><h1>감사로그</h1><p>권한·계정·메뉴·업무 변경의 행위자와 결과를 확인합니다.</p></div><button className="ghost-button" onClick={load}><RefreshCw /> 새로고침</button></header>{error ? <div role="alert"><AlertTriangle /> {error}</div> : null}<section className="access-admin-card"><div className="access-admin-table">{events.map((event) => <article key={String(event.id)}><div><strong>{String(event.action)}</strong><span>{String(event.username || 'system')} · {String(event.path)} · {String(event.occurred_at)}</span></div><b>{String(event.status_code)}</b></article>)}</div></section></section>
}
