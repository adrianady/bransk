import { describe, expect, it } from 'vitest'
import { collectSubjectTags, isDateTag, splitTags, subjectTags } from './tags'

describe('tags helpers', () => {
  it('recognises ISO dates, including negative years', () => {
    expect(isDateTag('1973-01-01')).toBe(true)
    expect(isDateTag('-0350-01-01')).toBe(true)
    expect(isDateTag('study')).toBe(false)
    expect(isDateTag('2023')).toBe(false)
  })

  it('splits, trims and de-duplicates without reordering', () => {
    expect(splitTags(' study, neurobiology ,study,  pdf ')).toEqual([
      'study',
      'neurobiology',
      'pdf',
    ])
  })

  it('drops date tags but keeps real subjects', () => {
    expect(subjectTags('1973-01-01, study, neurobiology')).toEqual(['study', 'neurobiology'])
  })

  it('handles null/empty input', () => {
    expect(splitTags(null)).toEqual([])
    expect(subjectTags(undefined)).toEqual([])
    expect(collectSubjectTags([])).toEqual([])
  })

  it('collects a sorted unique subject list across entries', () => {
    const entries = [
      { tags: 'memory, 2020-01-01' },
      { tags: 'memory, sleep' },
      { tags: 'study' },
    ]
    expect(collectSubjectTags(entries)).toEqual(['memory', 'sleep', 'study'])
  })
})
