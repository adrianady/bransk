// Shared DOI helper — replaces the verbatim `getStudyDoi` duplicates that
// lived in StudiesView.tsx and StudyPdfModal.tsx. Priority: an explicit
// `doi` field wins, otherwise the first `10.xxxx/...` string found in the
// source URL, then in the body text (`content`, falling back to the list
// view's `content_preview`).
import type { Entry } from './types'

const DOI_PATTERN = /10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+/

/** Best-effort DOI for a study entry, or null when none is recorded. */
export function getStudyDoi(study: Entry | null | undefined): string | null {
  if (!study) return null
  if (study.doi && study.doi.trim()) return study.doi.trim()
  const fromSource = (study.source || '').match(DOI_PATTERN)
  if (fromSource) return fromSource[0]
  const fromContent = (study.content || study.content_preview || '').match(DOI_PATTERN)
  return fromContent ? fromContent[0] : null
}
