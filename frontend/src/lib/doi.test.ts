import { describe, expect, it } from 'vitest'
import { getStudyDoi } from './doi'
import type { Entry } from './types'

function entry(overrides: Partial<Entry>): Entry {
  return { id: 1, title: 'study', source: '', tags: '' , ...overrides } as Entry
}

describe('getStudyDoi', () => {
  it('prefers an explicit doi field, trimmed', () => {
    expect(getStudyDoi(entry({ doi: ' 10.1000/xyz123 ' }))).toBe('10.1000/xyz123')
  })

  it('prefers the doi field over a DOI found in the source URL', () => {
    expect(
      getStudyDoi(entry({ doi: '10.1000/explicit', source: 'https://doi.org/10.2000/other' })),
    ).toBe('10.1000/explicit')
  })

  it('extracts a DOI embedded in a source URL', () => {
    expect(getStudyDoi(entry({ source: 'https://doi.org/10.1037/arc0000123' }))).toBe(
      '10.1037/arc0000123',
    )
  })

  it('extracts a DOI mentioned in the abstract/body text', () => {
    expect(
      getStudyDoi(entry({ content: 'Results were replicated (see doi:10.1109/5.771073 for details).' })),
    ).toBe('10.1109/5.771073')
  })

  it('falls back to content_preview like the study list does', () => {
    expect(getStudyDoi(entry({ content_preview: 'see 10.1000/preview-doi here' }))).toBe(
      '10.1000/preview-doi',
    )
  })

  it('returns null when there is no DOI anywhere', () => {
    expect(getStudyDoi(entry({ source: 'https://example.com/paper', content: 'no identifiers' }))).toBeNull()
  })

  it('returns null for a null study', () => {
    expect(getStudyDoi(null)).toBeNull()
  })
})
