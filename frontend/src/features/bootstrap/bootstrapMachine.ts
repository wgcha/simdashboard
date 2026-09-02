export type WorkspaceBootstrapState =
  | { status: 'idle'; generation: number }
  | { status: 'loading'; generation: number; userKey: string }
  | { status: 'resolved'; generation: number; userKey: string; resolvedKind: 'empty' | 'setup' | 'ready' }
  | { status: 'failed'; generation: number; userKey: string; message: string }

export type WorkspaceBootstrapEvent =
  | { type: 'RESET'; generation: number }
  | { type: 'START'; generation: number; userKey: string }
  | { type: 'RESOLVE'; generation: number; userKey: string; resolvedKind: 'empty' | 'setup' | 'ready' }
  | { type: 'REJECT'; generation: number; userKey: string; message: string }

export const initialWorkspaceBootstrapState: WorkspaceBootstrapState = { status: 'idle', generation: 0 }

export function workspaceBootstrapReducer(
  state: WorkspaceBootstrapState,
  event: WorkspaceBootstrapEvent,
): WorkspaceBootstrapState {
  if (event.generation < state.generation) return state
  switch (event.type) {
    case 'RESET': return { status: 'idle', generation: event.generation }
    case 'START': return { status: 'loading', generation: event.generation, userKey: event.userKey }
    case 'RESOLVE': return { status: 'resolved', generation: event.generation, userKey: event.userKey, resolvedKind: event.resolvedKind }
    case 'REJECT': return { status: 'failed', generation: event.generation, userKey: event.userKey, message: event.message }
  }
}
