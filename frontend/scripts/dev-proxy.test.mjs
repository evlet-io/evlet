import { describe, expect, it } from 'vitest'
import { backendAuthority } from './dev-proxy.mjs'

describe('dev proxy authority', () => {
  it('maps loopback dev-server authorities to the backend port', () => {
    expect(backendAuthority('127.0.0.1:5173', ['5173', '4173'], 8020)).toBe('127.0.0.1:8020')
    expect(backendAuthority('localhost:4173', ['5173', '4173'], 8020)).toBe('localhost:8020')
  })
  it('leaves other hosts and ports to the backend checks', () => {
    for (const value of ['evil.example:5173', 'alpha.localhost:5173', '127.0.0.1:9999', '127.0.0.1', '127.0.0.1:5173.evil', undefined])
      expect(backendAuthority(value, ['5173', '4173'], 8020)).toBeNull()
  })
})
