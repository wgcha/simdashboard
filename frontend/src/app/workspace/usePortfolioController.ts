import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import { DEFAULT_PORTFOLIO_LAYOUT, loadPortfolioLayout } from '../../features/layouts/layoutDefaults'
import type { PortfolioLayout } from '../../types'

type Options = {
  projectId: string
  onError: (message: string) => void
  onNotice: (message: string) => void
  onCloseEditor: () => void
}

/** Owns the persisted operational-dashboard layout and its reversible draft. */
export function usePortfolioController({ projectId, onError, onNotice, onCloseEditor }: Options) {
  const [layout, setLayout] = useState<PortfolioLayout>(loadPortfolioLayout)
  const [layoutVersion, setLayoutVersion] = useState(1)
  const beforeEdit = useRef<PortfolioLayout | null>(null)

  useEffect(() => {
    if (!projectId || beforeEdit.current) return
    let active = true
    api.workspaceLayout(projectId, 'portfolio').then((stored) => {
      if (!active) return
      setLayout(stored.definition)
      setLayoutVersion(stored.version)
    }).catch(() => undefined)
    return () => { active = false }
  }, [projectId])

  const beginEditing = useCallback(() => {
    beforeEdit.current = { ...layout, chartOrder: [...layout.chartOrder] }
  }, [layout])

  const cancelEditing = useCallback(() => {
    if (beforeEdit.current) setLayout(beforeEdit.current)
    beforeEdit.current = null
    onCloseEditor()
  }, [onCloseEditor])

  const save = useCallback(async () => {
    try {
      const stored = await api.saveWorkspaceLayout(projectId, 'portfolio', layout)
      setLayout(stored.definition)
      setLayoutVersion(stored.version)
      beforeEdit.current = null
      onCloseEditor()
      onNotice(`운영 대시보드 설정 v${stored.version}을 저장했습니다.`)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '운영 대시보드 설정을 저장하지 못했습니다.')
    }
  }, [layout, onCloseEditor, onError, onNotice, projectId])

  const reset = useCallback(() => {
    setLayout({ ...DEFAULT_PORTFOLIO_LAYOUT, chartOrder: [...DEFAULT_PORTFOLIO_LAYOUT.chartOrder] })
    onNotice('기본 배치를 미리 적용했습니다. 저장하거나 취소할 수 있습니다.')
  }, [onNotice])

  return { beginEditing, cancelEditing, layout, layoutVersion, reset, save, setLayout }
}
