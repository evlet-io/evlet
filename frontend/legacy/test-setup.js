import { vi } from 'vitest'

// jsdom has no layout engine. Supply only the browser layout APIs the real
// PrimeVue components require; component behavior is not stubbed.
window.matchMedia = vi.fn().mockImplementation(query => ({
  matches: false, media: query, onchange: null,
  addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
}))
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Element.prototype.scrollIntoView = vi.fn()
