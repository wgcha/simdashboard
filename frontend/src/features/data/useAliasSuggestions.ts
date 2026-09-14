import { useCallback, useEffect, useRef, useState } from 'react'
import { semanticVocabularyApi, type SemanticVocabularyEntry, type SemanticVocabularyResolveMatch, type VocabularyTargetKind } from '../../shared/api/semanticVocabulary'

export function useAliasSuggestions() {
  const generation = useRef(0)
  const [loading, setLoading] = useState(false)
  const [match, setMatch] = useState<SemanticVocabularyResolveMatch | null>(null)
  const [error, setError] = useState('')
  const latestQuery = useRef<{ terms: string[]; targetKinds: VocabularyTargetKind[]; scopeProjectId?: string } | null>(null)

  useEffect(() => () => {
    generation.current += 1
    latestQuery.current = null
  }, [])

  const suggest = useCallback(async (terms: string[], targetKinds: VocabularyTargetKind[], scopeProjectId?: string) => {
    const current = ++generation.current
    latestQuery.current = { terms, targetKinds, scopeProjectId }
    setLoading(true)
    setError('')
    setMatch(null)
    try {
      const response = await semanticVocabularyApi.resolve(terms, targetKinds, scopeProjectId)
      if (current !== generation.current) return null
      const next = response.matches[0] ?? { term: terms[0] ?? '', status: 'UNMAPPED' as const, candidates: [] }
      setMatch(next)
      return next
    } catch (reason) {
      if (current !== generation.current) return null
      setError(reason instanceof Error ? reason.message : '별칭 후보를 찾지 못했습니다.')
      return null
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [])

  const confirm = useCallback(async (candidate: SemanticVocabularyEntry) => {
    const query = latestQuery.current
    if (!query) return false
    const current = ++generation.current
    setLoading(true)
    setError('')
    try {
      const response = await semanticVocabularyApi.resolve(query.terms, query.targetKinds, query.scopeProjectId)
      if (current !== generation.current) return false
      const confirmed = response.matches[0]?.candidates.find((item) => item.id === candidate.id && item.revision === candidate.revision)
      if (!confirmed) {
        setError('후보가 변경되었습니다. 다시 별칭을 검색하세요.')
        setMatch(null)
        return false
      }
      return true
    } catch (reason) {
      if (current !== generation.current) return false
      setError(reason instanceof Error ? reason.message : '후보를 확인하지 못했습니다.')
      return false
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [])

  const clear = useCallback(() => {
    generation.current += 1
    setMatch(null)
    setError('')
    setLoading(false)
    latestQuery.current = null
  }, [])

  return { clear, confirm, error, loading, match, suggest }
}

export type AliasSuggestionSelection = (entry: SemanticVocabularyEntry) => void
