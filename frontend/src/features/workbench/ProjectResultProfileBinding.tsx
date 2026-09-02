import { Check, LayoutDashboard, Save } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { resultLayoutApi, type AnalysisTemplateVersion, type ResultProfile } from '../../shared/api/resultLayouts'
import { workbenchApi } from './api'
import { ResultProfileConfiguration } from './ResultProfileConfiguration'
import { resultProfileValidation } from './resultProfileContracts'
import type { WorkbenchRequestType } from './types'

export function ProjectResultProfileBinding({ projectId }: { projectId: string }) {
  const [requestTypes, setRequestTypes] = useState<WorkbenchRequestType[]>([])
  const [templates, setTemplates] = useState<AnalysisTemplateVersion[]>([])
  const [requestTypeKey, setRequestTypeKey] = useState('')
  const [profile, setProfile] = useState<ResultProfile | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [profileLoading, setProfileLoading] = useState(false)
  const [profileKey, setProfileKey] = useState("")
  const profileLoadToken = useRef(0)

  useEffect(() => {
    Promise.all([workbenchApi.requestTypes(), resultLayoutApi.analysisTemplates(false, projectId)])
      .then(([types, availableTemplates]) => { setRequestTypes(types); setTemplates(availableTemplates) })
      .catch((reason) => setError(reason instanceof Error ? reason.message : '결과 구성 정보를 불러오지 못했습니다.'))
  }, [projectId])

  const selectedType = requestTypes.find((item) => item.id + ":" + item.version === requestTypeKey) ?? null
  const selectedProfileKey = selectedType ? projectId + ":" + selectedType.id + ":" + selectedType.version : ""

  useEffect(() => {
    const token = profileLoadToken.current + 1
    profileLoadToken.current = token
    setProfile(null)
    setProfileKey("")
    setSaving(false)
    setNotice('')
    if (!selectedType) {
      setProfileLoading(false)
      return
    }
    setProfileLoading(true)
    setError('')
    let cancelled = false
    void resultLayoutApi.resultProfile(selectedType.id, selectedType.version, projectId)
      .then((saved) => { if (!cancelled && profileLoadToken.current === token) { setProfile(saved); setProfileKey(selectedProfileKey) } })
      .catch((reason) => { if (!cancelled && profileLoadToken.current === token) setError(reason instanceof Error ? reason.message : '기존 결과 구성을 불러오지 못했습니다.') })
      .finally(() => { if (!cancelled && profileLoadToken.current === token) setProfileLoading(false) })
    return () => { cancelled = true }
  }, [selectedProfileKey])

  const validationError = resultProfileValidation(profile, [])

  const save = async () => {
    if (!selectedType || !profile || profileKey !== selectedProfileKey || validationError || profileLoading) return
    const saveToken = profileLoadToken.current
    const saveKey = selectedProfileKey
    setSaving(true); setError(""); setNotice("")
    try {
      const saved = await resultLayoutApi.saveProjectResultProfile(projectId, selectedType.id, selectedType.version, { template_id: profile.template_id, template_version: profile.template_version, included_widget_ids: profile.included_widget_ids, overrides: profile.overrides, required_data_contracts: profile.required_data_contracts })
      if (profileLoadToken.current === saveToken && selectedProfileKey === saveKey) {
        setProfile(saved)
        setNotice(selectedType.display_name + "의 프로젝트 결과 구성을 저장했습니다. 새 의뢰부터 이 레이아웃 snapshot을 사용합니다.")
      }
    } catch (reason) {
      if (profileLoadToken.current === saveToken && selectedProfileKey === saveKey) setError(reason instanceof Error ? reason.message : "프로젝트 결과 구성을 저장하지 못했습니다.")
    } finally {
      if (profileLoadToken.current === saveToken && selectedProfileKey === saveKey) setSaving(false)
    }
  }

  const updateProfile = (next: ResultProfile | null) => {
    if (!selectedProfileKey) return
    profileLoadToken.current += 1
    setProfileLoading(false)
    setProfileKey(selectedProfileKey)
    setProfile(next ? { ...next, request_type_id: selectedType?.id ?? "", request_type_version: selectedType?.version ?? 0 } : null)
  }

  return <main className="workbench-admin-page project-result-profile-page" data-testid="project-result-profile-binding">
    <section className="workbench-admin-hero"><div><span><LayoutDashboard /> PROJECT ADMIN</span><h1>프로젝트 결과 구성</h1><p>전역 작업 유형은 수정하지 않고, 이 프로젝트에서 새 의뢰가 사용할 결과 템플릿과 위젯만 연결합니다.</p></div></section>
    {error && <div className="workbench-service-error" role="alert">{error}</div>}
    {notice && <div className="workbench-admin-notice" role="status"><Check aria-hidden="true" /> {notice}</div>}
    <section className="workbench-admin-form"><label><span>작업 유형 버전</span><select aria-label="프로젝트 결과 구성 작업 유형" value={requestTypeKey} onChange={(event) => { profileLoadToken.current += 1; setProfile(null); setNotice(''); setRequestTypeKey(event.target.value) }}><option value="">작업 유형을 선택하세요</option>{requestTypes.map((item) => <option key={`${item.id}:${item.version}`} value={`${item.id}:${item.version}`}>{item.display_name} · v{item.version}</option>)}</select></label>
      {profileLoading && <p role="status">선택한 작업 유형의 결과 구성을 불러오는 중입니다.</p>}
      {validationError && <p className="result-profile-validation" role="alert">{validationError}</p>}
      {selectedType && <ResultProfileConfiguration templates={templates} value={profile} onChange={updateProfile} />}
      <button className="workbench-admin-save" disabled={!profile || profileKey !== selectedProfileKey || saving || profileLoading || Boolean(validationError)} onClick={() => void save()}><Save aria-hidden="true" /> {saving ? '저장 중…' : '프로젝트 결과 구성 저장'}</button>
    </section>
  </main>
}
