import { BrainCircuit } from 'lucide-react'
import { joinSpdmPath, physicsAiImportHref } from '../../shared/api/physicsaiLinkModel'
import { usePhysicsAiLink } from '../../shared/hooks/usePhysicsAiLink'

type Props = { projectId: string; /** SPDM root-relative folder of the selected Case / Run Option. */ relativePath: string | null | undefined }

/** D8: opens PhysicsAI's import page for this SPDM folder in a new window. Hidden while the link flag is off. */
export function PhysicsAiImportLink({ projectId, relativePath }: Props) {
  const link = usePhysicsAiLink()
  const href = physicsAiImportHref(link, relativePath, projectId)
  if (!href) return null
  const spdmPath = joinSpdmPath(link.spdmRoot, relativePath) ?? ''
  return <a className="case-report__trigger" style={{ textDecoration: 'none' }} href={href} target="_blank" rel="noopener noreferrer" title={`AI 학습 데이터로 보내기 (새 창, 파일은 옮기지 않음)\n${spdmPath}`}><BrainCircuit aria-hidden="true" /><span>AI 학습 데이터로 보내기</span></a>
}
