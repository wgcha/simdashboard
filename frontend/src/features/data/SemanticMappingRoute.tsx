import { useState } from 'react'

import { SemanticMappingWorkspace } from './SemanticMappingWorkspace'
import { LegacyFolderSchemaWorkspace } from './FolderSchemaWorkspace'
import { FolderEnvironmentWorkspace } from './FolderEnvironmentWorkspace'

export function SemanticMappingRoute({ isVocabularyAdmin = false, canImportResults = true, canImportForProject, selectedProjectId = '', selectedRequestId = '' }: { isVocabularyAdmin?: boolean; canImportResults?: boolean; canImportForProject?: (projectId: string) => boolean; selectedProjectId?: string; selectedRequestId?: string }) {
  const [mode, setMode] = useState<'environment' | 'semantic' | 'legacy'>('environment')
  return <>
    <div className="schema-mode-switch" role="tablist" aria-label="결과 스키마 화면">
      <button className={mode === 'environment' ? 'active' : ''} onClick={() => setMode('environment')} role="tab" aria-selected={mode === 'environment'}>폴더 연결·규칙</button>
      <button className={mode === 'semantic' ? 'active' : ''} onClick={() => setMode('semantic')} role="tab" aria-selected={mode === 'semantic'}>의미 매핑</button>
      <button className={mode === 'legacy' ? 'active' : ''} onClick={() => setMode('legacy')} role="tab" aria-selected={mode === 'legacy'}>기존 폴더 스키마</button>
    </div>
    {mode === 'environment' ? <FolderEnvironmentWorkspace selectedProjectId={selectedProjectId} selectedRequestId={selectedRequestId} /> : mode === 'semantic' ? <SemanticMappingWorkspace onLegacy={() => setMode('legacy')} canImportResults={canImportResults} canImportForProject={canImportForProject} isVocabularyAdmin={isVocabularyAdmin} selectedProjectId={selectedProjectId} /> : <LegacyFolderSchemaWorkspace />}
  </>
}
