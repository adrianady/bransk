import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ensurePersistentStorage } from './storage'

function setNavigatorStorage(value: unknown) {
  Object.defineProperty(window.navigator, 'storage', {
    value,
    configurable: true,
    writable: true,
  })
}

function clearNavigatorStorage() {
  // jsdom ships without navigator.storage; restore that default.
  Object.defineProperty(window.navigator, 'storage', {
    value: undefined,
    configurable: true,
    writable: true,
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

afterEach(() => {
  clearNavigatorStorage()
})

describe('ensurePersistentStorage', () => {
  it('never throws and resolves false when the Storage API is missing', async () => {
    clearNavigatorStorage()
    await expect(ensurePersistentStorage()).resolves.toBe(false)
  })

  it('skips persist() when storage is already persistent', async () => {
    const persist = vi.fn()
    setNavigatorStorage({ persisted: async () => true, persist })
    await expect(ensurePersistentStorage()).resolves.toBe(true)
    expect(persist).not.toHaveBeenCalled()
  })

  it('calls persist() once when not yet persistent, then stays quiet', async () => {
    const persist = vi.fn(async () => true)
    setNavigatorStorage({ persisted: async () => false, persist })
    await expect(ensurePersistentStorage()).resolves.toBe(true)
    expect(persist).toHaveBeenCalledTimes(1)
    // Second startup attempt must not prompt again (localStorage guard).
    await expect(ensurePersistentStorage()).resolves.toBe(false)
    expect(persist).toHaveBeenCalledTimes(1)
  })
})
