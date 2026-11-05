// Tag handling shared by the Graph and Timeline filters.
//
// The `entries.tags` column is a comma-separated string that historically also
// collected a bare ISO date (the study's year) beside the real tags, e.g.
// "1973-01-01, study, neurobiology, pdf". A date is not a tag: it belongs to
// `entry_date`, and showing it in the "All tags" filter turned a subject list
// into a list of years. Filter them out wherever a tag list is built.

/** True for a bare ISO date such as 1973-01-01 or -0350-01-01 (350 BCE). */
export const ISO_DATE_RE = /^-?\d{4}-\d{2}-\d{2}$/

export function isDateTag(tag: string): boolean {
  return ISO_DATE_RE.test(tag)
}

/** Split a comma-separated tag string into clean, de-duplicated tags. */
export function splitTags(raw: string | null | undefined): string[] {
  if (!raw) return []
  const seen = new Set<string>()
  for (const part of raw.split(',')) {
    const t = part.trim()
    if (t) seen.add(t)
  }
  return [...seen]
}

/** Only the tags that carry meaning: no dates, no empties. */
export function subjectTags(raw: string | null | undefined): string[] {
  return splitTags(raw).filter(t => !isDateTag(t))
}

/** Build a sorted list of subject tags from entries, for a filter dropdown. */
export function collectSubjectTags(entries: { tags: string }[]): string[] {
  const set = new Set<string>()
  for (const e of entries) for (const t of subjectTags(e.tags)) set.add(t)
  return [...set].sort()
}
