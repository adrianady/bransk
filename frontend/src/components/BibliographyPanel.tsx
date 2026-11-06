import { useEffect, useState } from 'react'
import { BookMarked, Copy, Download, Loader2, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { BibliographyReference } from '../lib/types'

/**
 * Thesis bibliography, derived from the study library (not retyped): every
 * study becomes an APA reference, the ones the chapters actually mention are
 * flagged, and the whole library exports as BibTeX/RIS for Zotero/Word.
 */
export function BibliographyPanel() {
  const toast = useToast()
  const [refs, setRefs] = useState<BibliographyReference[] | null>(null)
  const [citedCount, setCitedCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState<'bibtex' | 'ris' | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.citations.bibliography()
      setRefs(data.references)
      setCitedCount(data.cited_count)
    } catch (err) {
      toast.error('Bibliografia nu s-a putut încărca: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const doExport = async (format: 'bibtex' | 'ris') => {
    setExporting(format)
    try {
      await api.citations.export(format)
      toast.success(format === 'bibtex' ? 'BibTeX descărcat (.bib)' : 'RIS descărcat (.ris)')
    } catch (err) {
      toast.error('Exportul a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setExporting(null)
    }
  }

  const copy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text)
      toast.success(`${label} copiat în clipboard`)
    } catch {
      toast.error('Copierea a eșuat — selectează textul manual')
    }
  }

  const cited = refs?.filter(r => r.cited) ?? []
  const rest = refs?.filter(r => !r.cited) ?? []

  const renderItem = (r: BibliographyReference) => (
    <div key={r.entry_id} className="flex items-start gap-2 py-2 border-b border-gray-100 dark:border-gray-800 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-800 dark:text-gray-200 break-words">{r.apa}</p>
        <div className="flex gap-2 mt-1">
          {r.cited && (
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300">
              citat în lucrare
            </span>
          )}
          {r.has_doi && (
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
              DOI
            </span>
          )}
        </div>
      </div>
      <div className="flex gap-1 shrink-0">
        <button
          onClick={async () => copy(r.apa, 'Referința')}
          className="btn-secondary text-xs px-2 py-1"
          title="Copiază referința APA"
        >
          <Copy className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 items-center">
        <button onClick={load} disabled={loading} className="btn-secondary text-sm">
          {loading ? <><Loader2 className="w-4 h-4 animate-spin" /> Se încarcă…</> : <><RefreshCw className="w-4 h-4" /> Reîmprospătează</>}
        </button>
        <button onClick={() => doExport('bibtex')} disabled={exporting !== null || !refs?.length} className="btn-secondary text-sm">
          {exporting === 'bibtex' ? <><Loader2 className="w-4 h-4 animate-spin" /> Export…</> : <><Download className="w-4 h-4" /> Export BibTeX (.bib)</>}
        </button>
        <button onClick={() => doExport('ris')} disabled={exporting !== null || !refs?.length} className="btn-secondary text-sm">
          {exporting === 'ris' ? <><Loader2 className="w-4 h-4 animate-spin" /> Export…</> : <><Download className="w-4 h-4" /> Export RIS (.ris)</>}
        </button>
        {refs && (
          <span className="text-sm text-gray-500 ml-auto">
            {refs.length} referințe · {citedCount} citate în text
          </span>
        )}
      </div>

      {refs === null && <div className="text-center py-8 text-gray-500">Se încarcă bibliografia…</div>}

      {refs !== null && refs.length === 0 && (
        <div className="card text-center py-10">
          <BookMarked className="w-8 h-8 mx-auto text-gray-400 mb-2" />
          <p className="text-gray-500">Biblioteca nu are încă studii. Cele adăugate sau descărcate prin cercetare apar aici automat.</p>
        </div>
      )}

      {refs !== null && cited.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold mb-2 text-gray-700 dark:text-gray-300">Citare în lucrare ({cited.length})</h3>
          {cited.map(renderItem)}
        </div>
      )}

      {refs !== null && rest.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold mb-2 text-gray-700 dark:text-gray-300">
            Bibliotecă ({rest.length})
            {cited.length > 0 && <span className="font-normal text-gray-500 ml-2">— menționează autorul și anul în capitole ca să marcheze o referință ca citată</span>}
          </h3>
          {rest.map(renderItem)}
        </div>
      )}
    </div>
  )
}
