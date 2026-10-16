import { useEffect, useState } from 'react'
import { physicsAiLinkApi } from '../api/physicsaiLink'
import { PHYSICSAI_LINK_OFF, type PhysicsAiLink } from '../api/physicsaiLinkModel'

// The flag is server-wide, so one successful read serves the whole page.
// Failures are not cached (e.g. a call made before login), and mean "off".
let cached: Promise<PhysicsAiLink> | null = null

function load(): Promise<PhysicsAiLink> {
  if (!cached) {
    cached = physicsAiLinkApi.get().catch(() => {
      cached = null
      return PHYSICSAI_LINK_OFF
    })
  }
  return cached
}

/** PhysicsAI link state; `enabled` stays false until the server confirms it. */
export function usePhysicsAiLink(active = true): PhysicsAiLink {
  const [link, setLink] = useState<PhysicsAiLink>(PHYSICSAI_LINK_OFF)
  useEffect(() => {
    if (!active) return
    let alive = true
    void load().then((value) => { if (alive) setLink(value) })
    return () => { alive = false }
  }, [active])
  return link
}
