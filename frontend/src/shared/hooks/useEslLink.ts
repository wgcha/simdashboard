import { useEffect, useState } from 'react'
import { eslLinkApi } from '../api/eslLink'
import { ESL_LINK_OFF, type EslLink } from '../api/eslLinkModel'

// The flag is server-wide, so one successful read serves the whole page.
// Failures are not cached (e.g. a call made before login), and mean "off".
let cached: Promise<EslLink> | null = null

function load(): Promise<EslLink> {
  if (!cached) {
    cached = eslLinkApi.get().catch(() => {
      cached = null
      return ESL_LINK_OFF
    })
  }
  return cached
}

/** ESL link state; `enabled` stays false until the server confirms it. */
export function useEslLink(active = true): EslLink {
  const [link, setLink] = useState<EslLink>(ESL_LINK_OFF)
  useEffect(() => {
    if (!active) return
    let alive = true
    void load().then((value) => { if (alive) setLink(value) })
    return () => { alive = false }
  }, [active])
  return link
}
