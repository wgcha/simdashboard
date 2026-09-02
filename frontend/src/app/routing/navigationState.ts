/** Keeps a requested destination only when the edit-loss guard permits it. */
export function resolveBlockedNavigation<Pending>(
  pending: Pending | null,
  confirmed: boolean,
): { pending: Pending | null; shouldProceed: boolean } {
  return { pending: confirmed ? pending : null, shouldProceed: confirmed }
}
