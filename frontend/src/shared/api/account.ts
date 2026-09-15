import { requireStringField, responseRecord } from './adapters'
import { apiClient, unwrapGenerated } from './client'

export type RegisterAccountInput = {
  username: string
  display_name: string
  password: string
}

export type RegisterAccountResult = {
  user_id: string
  username: string
  account_status: 'PENDING'
  message: string
}

export type ChangePasswordInput = {
  current_password: string
  new_password: string
}

function adaptRegistration(value: unknown): RegisterAccountResult {
  const item = responseRecord(value, 'register')
  requireStringField(item, 'user_id', 'register')
  requireStringField(item, 'username', 'register')
  requireStringField(item, 'account_status', 'register')
  requireStringField(item, 'message', 'register')
  if (item.account_status !== 'PENDING') throw new TypeError('register.account_status 응답 값이 올바르지 않습니다.')
  return item as unknown as RegisterAccountResult
}

function adaptPasswordChange(value: unknown): { ok: true } {
  const item = responseRecord(value, 'passwordChange')
  if (item.ok !== true) throw new TypeError('passwordChange.ok 응답 값이 올바르지 않습니다.')
  return { ok: true }
}

/** Account API adapters keep response validation close to the feature boundary. */
export const accountApi = {
  register: async (payload: RegisterAccountInput) =>
    adaptRegistration(unwrapGenerated(await apiClient.POST('/api/auth/register', { body: payload }))),
  changePassword: async (payload: ChangePasswordInput) =>
    adaptPasswordChange(unwrapGenerated(await apiClient.POST('/api/auth/password', { body: payload }))),
}
