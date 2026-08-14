import { useEffect, useState } from 'react'
import { AlertTriangle, Settings2 } from 'lucide-react'
import { api } from '../../api'
import type { AutomationTemplate } from '../../types'

export function AutomationTemplatesPage() {
  const [items,setItems]=useState<AutomationTemplate[]>([]); const [error,setError]=useState('')
  useEffect(()=>{api.automationTemplates().then(setItems).catch((reason)=>setError(reason instanceof Error?reason.message:'템플릿을 불러오지 못했습니다.'))},[])
  return <section className="template-page"><header><div><span>MODELING AUTOMATION</span><h1>자동화 템플릿</h1><p>하중 경우 아래의 모델링 자동화 버전·입력·실행 결과를 추적합니다.</p></div><strong>{items.length}개 실행 이력</strong></header>{error?<div className="portfolio-state error"><AlertTriangle/>{error}</div>:items.length?<div className="template-grid">{items.map((item)=><article key={item.id}><header><div><span>{item.analysis_type.replace('_',' ')}</span><h2>{item.template_name}</h2><p>{item.project_name} / {item.request_title}</p></div><b>{item.status}</b></header><div className="template-meta"><span>버전<strong>v{item.template_version}</strong></span><span>하중 경우<strong>{item.load_case_name}</strong></span><span>실행 일시<strong>{new Date(item.executed_at).toLocaleString('ko-KR')}</strong></span></div><section><div><h3>입력 파라미터</h3>{Object.entries(item.input).map(([key,value])=><p key={key}><code>{key}</code><span>{String(value)}</span></p>)}</div><div><h3>생성 모델</h3>{Object.entries(item.generated_model||{}).map(([key,value])=><p key={key}><code>{key}</code><span>{typeof value==='number'?value.toLocaleString():String(value)}</span></p>)}</div></section></article>)}</div>:<div className="portfolio-empty"><Settings2/><h2>선택 프로젝트의 자동화 실행 이력이 없습니다.</h2><p>하중 경우에 템플릿 실행을 연결하면 여기에 표시됩니다.</p></div>}</section>
}
