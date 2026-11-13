import { describe, expect, it } from 'vitest'
import {
  LIBRARY_FILTERS,
  MOBILE_QUICK_VIEW_IDS,
  filterForLibraryTab,
  isLibraryFilter,
  parseViewHash,
  tabForLibraryFilter,
  VIEW_NAV_ITEMS,
} from './navigation'

describe('view navigation contract', () => {
  it('keeps the nine established view ids and their hash routes', () => {
    const expected = [
      'project', 'timeline', 'graph', 'studies', 'theories',
      'entries', 'review', 'todos', 'thoughts',
    ]
    expect(VIEW_NAV_ITEMS.map(item => item.id)).toEqual(expected)
    expect(new Set(VIEW_NAV_ITEMS.map(item => item.id)).size).toBe(9)
    for (const id of expected) expect(parseViewHash(`#${id}`)).toBe(id)
  })

  it('keeps four mobile shortcuts and safely ignores unknown hashes', () => {
    expect(MOBILE_QUICK_VIEW_IDS).toEqual(['project', 'studies', 'graph', 'timeline'])
    expect(parseViewHash('')).toBeNull()
    expect(parseViewHash('#unknown')).toBeNull()
    expect(parseViewHash('#entries?sort=recent')).toBeNull()
  })
})

describe('library unification', () => {
  it('treats studies and entries as tabs of one library screen', async () => {
    const { isLibraryView, LIBRARY_TAB_VIEWS } = await import('./navigation')
    expect(LIBRARY_TAB_VIEWS).toEqual(['studies', 'entries'])
    expect(isLibraryView('studies')).toBe(true)
    expect(isLibraryView('entries')).toBe(true)
    expect(isLibraryView('project')).toBe(false)
    // The deep links keep working as tab presets.
    expect(parseViewHash('#studies')).toBe('studies')
    expect(parseViewHash('#entries')).toBe('entries')
  })

  it('maps the unified filter pills onto those tabs', () => {
    expect(LIBRARY_FILTERS).toEqual(['all', 'studies', 'notes', 'pdf', 'nopdf'])
    expect(tabForLibraryFilter('all')).toBe('entries')
    expect(tabForLibraryFilter('notes')).toBe('entries')
    expect(tabForLibraryFilter('studies')).toBe('studies')
    expect(tabForLibraryFilter('pdf')).toBe('studies')
    expect(tabForLibraryFilter('nopdf')).toBe('studies')
    expect(filterForLibraryTab('entries')).toBe('notes')
    expect(filterForLibraryTab('studies')).toBe('studies')
    expect(isLibraryFilter('nopdf')).toBe(true)
    expect(isLibraryFilter('bogus')).toBe(false)
  })
})
