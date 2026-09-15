import { useState } from 'react'

import { SemanticMappingWorkspace } from './SemanticMappingWorkspace'
import { LegacyFolderSchemaWorkspace } from './FolderSchemaWorkspace'

export function SemanticMappingRoute({ isVocabularyAdmin = false, selectedProjectId = '' }: { isVocabularyAdmin?: boolean; selectedProjectId?: string }) {
  const [mode, setMode] = useState<'semantic' | 'legacy'>('semantic')
  return <>
    <div className="schema-mode-switch" role="tablist" aria-label="결과 스키마 화면">
      <button className={mode === 'semantic' ? 'active' : ''} onClick={() => setMode('semantic')} role="tab" aria-selected={mode === 'semantic'}>의미 매핑</button>
      <button className={mode === 'legacy' ? 'active' : ''} onClick={() => setMode('legacy')} role="tab" aria-selected={mode === 'legacy'}>기존 폴더 스키마</button>
    </div>
    {mode === 'semantic' ? <SemanticMappingWorkspace onLegacy={() => setMode('legacy')} isVocabularyAdmin={isVocabularyAdmin} selectedProjectId={selectedProjectId} /> : <LegacyFolderSchemaWorkspace />}
  </>
}
