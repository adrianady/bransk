import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import {
  AlertTriangle, BookOpen, ChevronRight, Clock, Check, Copy, ExternalLink,
  FileText, Loader2, RefreshCw, TextQuote, Trash2, X,
} from 'lucide-react';
import { api } from '../lib/api';
import { useToast } from './Toast';
import { NoteMap } from './NoteMap';
import type {
  NoteClaim, NoteClaimStatus, NoteDossier, NoteDossierSummary, NoteSection,
} from '../lib/types';

type Tab = 'dossiers' | 'detail';

const STATUS_LABEL: Record<NoteClaimStatus, string> = {
  verificat: 'Verificat',
  verificat_cu_nuanta: 'Verificat cu nuanță',
  incomplet: 'Incomplet',
  subestimat: 'Subestimată',
  corectat: 'Corectată',
  depasit: 'Depășită',
  neverificat: 'Neverificat',
};

const STATUS_COLOR: Record<NoteClaimStatus, string> = {
  verificat: 'text-verdigris border-verdigris/20 bg-verdigris-soft',
  verificat_cu_nuanta: 'text-chrono-600 border-chrono-300/40 bg-chrono-50 dark:bg-chrono-950/20',
  incomplet: 'text-brass border-brass/20 bg-brass-soft',
  subestimat: 'text-brass border-brass/20 bg-brass-soft',
  corectat: 'text-madder border-madder/20 bg-madder-soft',
  depasit: 'text-madder border-madder/20 bg-madder-soft',
  neverificat: 'text-gray-500 border-gray-200/60 bg-gray-100 dark:bg-gray-800 dark:border-gray-700/60',
};

function statusChip(status: NoteClaimStatus) {
  const style = STATUS_COLOR[status] ?? STATUS_COLOR.neverificat;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-0.5 text-[10px] font-semibold border ${style}`}
      title={STATUS_LABEL[status]}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />
      {STATUS_LABEL[status]}
    </span>
  );
}

function dossierStatusChip(status: NoteDossierSummary['status']) {
  const style: Record<string, string> = {
    draft: 'text-gray-500 border-gray-200/60 bg-gray-100 dark:bg-gray-800 dark:border-gray-700/60',
    partial: 'text-brass border-brass/20 bg-brass-soft',
    verificat: 'text-verdigris border-verdigris/20 bg-verdigris-soft',
  };
  const label: Record<string, string> = {
    draft: 'Ciornă',
    partial: 'Parțial verificat',
    verificat: 'Verificat',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-0.5 text-[10px] font-semibold border ${style[status] ?? style.draft}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />
      {label[status] ?? 'Nespecificat'}
    </span>
  );
}

function barWidth(count: number, total: number) {
  if (!total) return 0;
  return Math.min(100, Math.round((count / total) * 100));
}

function formatDate(iso: string | undefined) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('ro-RO', {
      day: 'numeric', month: 'short', year: 'numeric',
    });
  } catch {
    return iso;
  }
}

function keyValueFromSourceSheet(source_sheet_json: string) {
  // Lines look like "Cheie: valoare". Split on the first colon.
  const rows: { key: string; value: string }[] = [];
  const raw = (source_sheet_json || '').trim();
  if (!raw) return rows;
  for (const line of raw.split(/\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const colon = trimmed.indexOf(':');
    if (colon < 0) {
      rows.push({ key: trimmed, value: '' });
    } else {
      rows.push({ key: trimmed.slice(0, colon).trim(), value: trimmed.slice(colon + 1).trim() });
    }
  }
  return rows;
}

function renderSectionBody(body_md: string) {
  // Plain paragraphs, no markdown parser dependency. Split on blank lines.
  const paragraphs = body_md.split(/\n\s*\n/).filter(p => p.trim().length > 0);
  return (
    <div className="space-y-3 text-sm text-gray-700 dark:text-gray-300 leading-relaxed">
      {paragraphs.map((p, i) => (
        <p key={i} className="break-words whitespace-pre-wrap">
          {p.trim()}
        </p>
      ))}
    </div>
  );
}

interface Props {
  /** Optional: parent opens the panel already on a given dossier id. */
  openOnDossierId?: number | null;
}

export function NotesPanel({ openOnDossierId }: Props) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('dossiers');
  const [dossiers, setDossiers] = useState<NoteDossierSummary[]>([]);
  const [detail, setDetail] = useState<NoteDossier | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<{ kind: string; id: number } | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [captureResult, setCaptureResult] = useState<NoteClaim[] | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.noteDossiers.list();
      setDossiers(res.dossiers || []);
    } catch (err: any) {
      toast.error('Dosarele de notițe nu s-au putut încărca: ' + (err?.message || 'eroare necunoscută'));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (openOnDossierId && tab === 'dossiers') {
      const d = dossiers.find(x => x.id === openOnDossierId);
      if (d) openDetail(d.id);
    }
  }, [openOnDossierId, dossiers, tab]);

  const openDetail = useCallback(async (id: number) => {
    setTab('detail');
    setLoading(true);
    try {
      setDetail(await api.noteDossiers.get(id));
    } catch (err: any) {
      toast.error('Dosarul nu s-a putut deschide: ' + (err?.message || 'eroare necunoscută'));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const goBack = useCallback(() => {
    setTab('dossiers');
    setDetail(null);
    setConfirmDeleteId(null);
  }, []);

  const handleVerify = useCallback(async (recheck: boolean) => {
    if (!detail?.dossier.id) return;
    setSaving({ kind: 'verify', id: detail.dossier.id });
    try {
      const updated = await api.noteDossiers.verify(detail.dossier.id, { recheck });
      setDetail(updated);
      setDossiers(prev => prev.map(d => d.id === updated.dossier.id ? { ...d, ...updated.dossier } : d));
      toast.success(recheck
        ? 'Reverificare completă terminată.'
        : 'Dosarul a fost reverificat.');
    } catch (err: any) {
      toast.error('Reverificarea a eșuat: ' + (err?.message || 'eroare necunoscută'));
    } finally {
      setSaving(null);
    }
  }, [detail?.dossier.id, toast]);

  const handleDelete = useCallback(async () => {
    if (!detail?.dossier.id) return;
    setSaving({ kind: 'delete', id: detail.dossier.id });
    try {
      await api.noteDossiers.remove(detail.dossier.id);
      setDossiers(prev => prev.filter(d => d.id !== detail!.dossier.id));
      goBack();
      toast.success('Dosarul a fost șters.');
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare necunoscută'));
    } finally {
      setSaving(null);
      setConfirmDeleteId(null);
    }
  }, [detail?.dossier.id, goBack, toast]);

  const handlePatchClaim = useCallback(async (claimId: number, body: Partial<NoteClaim>) => {
    setSaving({ kind: 'claim', id: claimId });
    try {
      const updated = await api.noteDossiers.patchClaim(claimId, body);
      setDetail(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          claims: prev.claims.map(c => c.id === claimId ? { ...c, ...updated } : c),
        };
      });
      setDossiers(prev => prev.map(d => {
        if (d.id === detail?.dossier.id) {
          const count = detail?.claims.filter(c => c.status === 'verificat').length ?? d.claims_verificat;
          return { ...d, claims_verificat: count };
        }
        return d;
      }));
    } catch (err: any) {
      toast.error('Corecția nu s-a putut salva: ' + (err?.message || 'eroare necunoscută'));
    } finally {
      setSaving(null);
    }
  }, [detail?.dossier.id, toast]);

  const copyQuote = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      toast.info('Citat copiat.');
    }).catch(() => {});
  }, [toast]);

  const claimsForCorrections = useMemo(() => {
    if (!detail) return [];
    return detail.claims.filter(c =>
      c.status === 'corectat' || c.status === 'incomplet' ||
      c.status === 'subestimat' || c.status === 'depasit');
  }, [detail]);

  const engineIsNative = useMemo(() => {
    if (!detail?.claims.length) return true;
    return detail.claims.every(c => c.engine === 'native');
  }, [detail?.claims]);

  const claimsCount = detail?.claims.length ?? 0;
  const verificatCount = detail?.claims.filter(c => c.status === 'verificat').length ?? 0;

  const mapPayload = useMemo(() => {
    if (!detail) return undefined;
    return {
      dossier_id: detail.dossier.id,
      entry_id: detail.dossier.entry_id,
      title: detail.dossier.title,
      claims: detail.claims,
      edges: detail.edges,
    };
  }, [detail]);

  const refreshDetail = useCallback(async () => {
    if (!detail?.dossier.id) return;
    try {
      setDetail(await api.noteDossiers.get(detail.dossier.id));
    } catch (err: any) {
      toast.error('Dosarul nu s-a putut reîmprospăta: ' + (err?.message || 'eroare necunoscută'));
    }
  }, [detail?.dossier.id, toast]);

  return (
    <div className="space-y-3 h-full">
      {/* ── Header / create entry point ── */}
      <div className="card flex items-center justify-between gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <h3 className="font-serif font-semibold text-sm sm:text-base leading-snug">
            Dosare de notițe verificate
          </h3>
          <p className="text-[11px] text-muted">
            Un dosar colectează afirmațiile dintr-o sursă, verificate pagina cu pagina în PDF. Scrie o notiță în cititor sau în chat pentru a crea un dosar.
          </p>
        </div>
        <button
          onClick={() => { window.dispatchEvent(new Event('chronokg:open-library')); }}
          className="btn-secondary text-xs sm:text-sm px-3 py-1.5 flex items-center gap-1.5 min-h-[40px] touch-40"
        >
          <ExternalLink className="w-4 h-4 shrink-0" />
          <span className="hidden sm:inline">Deschide biblioteca</span>
        </button>
      </div>

      {tab === 'dossiers' ? (
        <>
          {/* ── Dossiers list ── */}
          <div className="space-y-2">
            {loading ? (
              <div className="card text-center py-10">
                <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2 text-chrono-500" />
                <p className="text-gray-500 text-sm">Se încarcă dosarele…</p>
              </div>
            ) : dossiers.length === 0 ? (
              <div className="card border-dashed border-line rounded-2xl p-8 text-center">
                <FileText className="w-10 h-10 text-muted mx-auto mb-3" />
                <p className="text-sm text-muted mb-3">Nu există încă dosare de notițe.</p>
                <p className="text-[11px] text-gray-400 max-w-xs mx-auto break-words">
                  Scrie o observație în cititorul PDF (butonul „Verifică și păstrează în dosar”) sau spune-i chat-ului ce să verifice. Fiecare notiță verificată creează un dosar cu afirmația, citatul și pagina.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {dossiers.map(d => (
                  <button
                    key={d.id}
                    onClick={() => openDetail(d.id)}
                    className="card w-full text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors min-w-0 group touch-40-neg"
                    aria-label={`Deschide dosarul ${d.title}`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2 mb-1">
                      <div className="flex-1 min-w-0">
                        <h4 className="font-semibold text-sm leading-snug break-words min-w-0">{d.title}</h4>
                        <p className="text-[11px] text-muted truncate">
                          {d.entry_authors ? `${d.entry_authors} · ${d.entry_year ?? '—'}` : '—'} · {d.entry_title}
                        </p>
                      </div>
                      <div className="shrink-0">
                        {dossierStatusChip(d.status)}
                      </div>
                    </div>
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div className="flex items-center gap-2 text-[11px]">
                        <span className="font-mono font-semibold text-ink">{d.claims_verificat}</span>
                        <span className="text-muted">de {d.claims_total} verificate</span>
                      </div>
                      <div className="flex-1 min-w-0 h-1.5 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden shrink-0">
                        <span
                          className="block h-full bg-verdigris-500 dark:bg-verdigris-400"
                          style={{ width: `${barWidth(d.claims_verificat, d.claims_total)}%` }}
                        />
                      </div>
                      <span className="text-[11px] text-muted">{formatDate(d.updated_at)}</span>
                    </div>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {Object.entries(d.by_status).map(([status, count]) => {
                        const s = status as NoteClaimStatus;
                        return (
                          <span key={status} className="badge text-[10px] px-2 py-1">
                            {STATUS_LABEL[s] ?? status}: {count}
                          </span>
                        );
                      })}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          {/* ── Detail header ── */}
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <button onClick={goBack} className="icon-btn text-muted hover:text-ink min-w-[40px] min-h-[40px] touch-40" aria-label="Înapoi la dosare">
              <X className="w-4 h-4" />
            </button>
            <h3 className="font-serif font-semibold text-sm sm:text-base leading-snug truncate flex-1 min-w-0">
              {detail?.dossier.title}
            </h3>
            <div className="flex items-center gap-1.5">
              {detail && saving?.kind === 'verify' && saving.id === detail.dossier.id && (
                <button disabled className="btn-secondary text-xs px-2 py-1 min-w-[40px] min-h-[40px] touch-40 opacity-60 cursor-not-allowed">
                  <Loader2 className="w-3 h-3 animate-spin" />
                </button>
              )}
              <button
                onClick={() => handleVerify(false)}
                disabled={saving?.kind === 'verify' && saving.id === detail?.dossier.id}
                className="btn-ghost text-xs px-2.5 py-1 min-w-[40px] min-h-[40px] touch-40 hover:bg-surface-2"
                title="Reverifică afirmațiile (NU mai reface cele deja verificate)"
              >
                <RefreshCw className="w-3 h-3 shrink-0" />
                <span className="hidden sm:inline">Reverifică</span>
              </button>
              <button
                onClick={() => handleVerify(true)}
                disabled={saving?.kind === 'verify' && saving.id === detail?.dossier.id}
                className="btn-secondary text-xs px-2.5 py-1 min-w-[40px] min-h-[40px] touch-40"
                title="Reverifică toate afirmațiile, inclusiv cele deja verificate"
              >
                <RefreshCw className="w-3 h-3 shrink-0" />
                <span className="hidden sm:inline">Complet</span>
              </button>
              {detail && (
                <button
                  onClick={() => setConfirmDeleteId(detail.dossier.id)}
                  className="text-[10px] text-madder hover:bg-madder-soft min-w-[40px] min-h-[40px] touch-40 rounded-lg p-1.5"
                  title="Șterge dosarul"
                >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
              )}
            </div>
          </div>

          {confirmDeleteId === detail?.dossier.id && (
            <div className="card border-madder/25 bg-madder-soft/40 p-3 space-y-3 mb-2">
              <p className="text-sm text-madder">Stergi permanent acest dosar și toate afirmațiile sale?</p>
              <div className="flex gap-2">
                <button onClick={handleDelete} className="btn-danger text-xs px-3 py-1.5 flex items-center gap-1.5">
                  <Trash2 className="w-3.5 h-3.5" /> Șterge dosarul
                </button>
                <button onClick={() => setConfirmDeleteId(null)} className="btn-ghost text-xs px-3 py-1.5">
                  Anulează
                </button>
              </div>
              <p className="text-[11px] text-muted">
                Acțiunea nu se poate anula. Nu șterge sursa (studiul) din bibliotecă — doar dosarul de verificare.
              </p>
            </div>
          )}

          <div className="space-y-3">
            {/* ── Source sheet ── */}
            <section className="card p-3 sm:p-4 space-y-2">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-muted shrink-0" />
                <h4 className="font-semibold text-sm text-ink">Fișa sursei</h4>
              </div>
              {detail ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {keyValueFromSourceSheet(detail.dossier.source_sheet_json).map((row, i) => (
                    <div key={i} className="flex gap-2 text-xs">
                      <span className="shrink-0 w-1/3 font-semibold text-muted truncate">{row.key}</span>
                      <span className="flex-1 min-w-0 break-words text-ink">{row.value || '—'}</span>
                    </div>
                  ))}
                  {keyValueFromSourceSheet(detail.dossier.source_sheet_json).length === 0 && (
                    <p className="text-[11px] text-gray-400 col-span-2">Fără detalii de fișă sursă.</p>
                  )}
                </div>
              ) : (
                <div className="text-center py-6 text-xs text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin mx-auto mb-1" /> Se încarcă…
                </div>
              )}
            </section>

            {/* ── Counters row ── */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {[
                { label: 'Total afirmații', value: String(detail?.summary.total ?? 0), color: 'text-muted' },
                { label: 'Verificate', value: String(detail?.summary.verificat ?? 0), color: 'text-verdigris' },
                { label: 'Neverificate', value: String(detail?.summary.neverificat ?? 0), color: 'text-gray-500' },
                { label: 'Corectări', value: String(claimsForCorrections.length), color: 'text-madder' },
              ].map((c, i) => (
                <div key={i} className="card !p-3 text-center border-line">
                  <div className={`text-xl font-bold tabular-nums ${c.color}`}>{c.value}</div>
                  <div className="text-[10px] text-muted leading-tight mt-0.5">{c.label}</div>
                </div>
              ))}
            </div>

            {/* ── Afirmații ── */}
            <section className="card p-3 sm:p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h4 className="font-semibold text-sm text-ink">Afirmații</h4>
                  <p className="text-[11px] text-muted">
                    Codul (ex. <span className="font-mono text-xs">A1</span>) vine de la nota ta; citatul e dovada, pagina e-printată te duce direct în text.
                  </p>
                </div>
                {detail && (
                  <span className="badge text-[10px] bg-surface-2 text-muted">
                    {claimsCount} afirmație{claimsCount !== 1 ? 'i' : ''}
                  </span>
                )}
              </div>
              {loading && !detail ? (
                <div className="text-center py-6 text-xs text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin mx-auto mb-1" /> Se încarcă afirmațiile…
                </div>
              ) : detail?.claims.length === 0 ? (
                <div className="text-center py-6 text-xs text-gray-400 border border-dashed rounded-xl p-3">
                  Nicio afirmație încă verificată în acest dosar.
                </div>
              ) : (
                <div className="space-y-2">
                  {(detail?.claims ?? []).map(claim => (
                    <div key={claim.id} className="card p-3 border-line hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors">
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-semibold text-xs bg-surface-2 dark:bg-gray-800 rounded px-2 py-0.5 border border-line">
                            {claim.code}
                          </span>
                          {statusChip(claim.status)}
                        </div>
                        <div className="flex items-center gap-1 text-[10px] text-muted shrink-0">
                          {claim.page_printed && (
                            <span className="flex items-center gap-1">
                              <TextQuote className="w-3 h-3 shrink-0" />
                              Pagina tipărită {claim.page_printed}
                            </span>
                          )}
                        </div>
                      </div>

                      <p className="text-sm text-ink leading-snug break-words mb-2">{claim.statement}</p>

                      {/* Quoted evidence box */}
                      <div className="border border-line rounded-lg p-3 bg-gray-50 dark:bg-gray-800/50 relative">
                        <div className="absolute -top-2.5 left-3 bg-canvas dark:bg-canvas px-1.5 text-[10px] font-semibold text-muted">
                          Citat verificat
                        </div>
                        <p className="text-sm text-ink leading-relaxed break-words whitespace-pre-wrap">
                          {claim.quote}
                        </p>
                        <div className="flex items-center justify-between gap-2 mt-2 pt-2 border-t border-line/60">
                          <div className="flex items-center gap-1.5 text-[10px] text-muted">
                            {claim.engine && claim.engine !== 'native' && (
                              <span className="flex items-center gap-1">
                                <span className="w-1.5 h-1.5 rounded-full bg-madder shrink-0" />
                                {claim.engine.toUpperCase()}
                              </span>
                            )}
                            <span>Verificare vizuală: {claim.visual_check === 'da' ? 'da' : claim.visual_check === 'nu' ? 'nu' : 'necesar'}</span>
                          </div>
                          <button
                            onClick={() => copyQuote(claim.quote)}
                            className="icon-btn p-1 text-muted hover:text-accent min-w-[36px] min-h-[36px] touch-40"
                            title="Copiază citatul"
                            aria-label="Copiază citatul"
                          >
                            <Copy className="w-3.5 h-3.5 shrink-0" />
                          </button>
                        </div>
                      </div>

                      {claim.correction && claim.correction.trim() && (
                        <div className="mt-2 p-2.5 rounded-lg bg-madder-soft border border-madder/20">
                          <p className="text-[11px] font-semibold text-madder mb-1 flex items-center gap-1">
                            <AlertTriangle className="w-3 h-3 shrink-0" /> Corecție
                          </p>
                          <p className="text-xs text-madder/90 break-words">{claim.correction}</p>
                        </div>
                      )}

                      <div className="mt-2 flex items-center gap-2 text-[10px] text-muted">
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3 shrink-0" />
                          {formatDate(claim.created_at)}
                        </span>
                        {claim.verified_at && (
                          <span className="flex items-center gap-1">
                            <Check className="w-3 h-3 text-verdigris shrink-0" />
                            verificat {formatDate(claim.verified_at)}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* ── Corecții table ── */}
            {claimsForCorrections.length > 0 && (
              <section className="card p-3 sm:p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <Trash2 className="w-4 h-4 text-madder shrink-0" />
                  <h4 className="font-semibold text-sm text-madder">Corecții la notițele tale</h4>
                </div>
                <p className="text-[11px] text-muted">
                  Aici vezi ce ai notat inițial, statusul dat de verificare și cum este în text — pentru a decide ce corectezi în lucrare.
                </p>
                <div className="markdown-table-wrap overflow-x-auto">
                  <table className="table-fixed w-full text-xs border-collapse border border-line">
                    <thead>
                      <tr className="bg-gray-50 dark:bg-gray-800/50">
                        <th className="border border-line p-2 text-left text-[10px] font-semibold text-muted w-16">Cod</th>
                        <th className="border border-line p-2 text-left text-[10px] font-semibold text-muted">Ce notasem</th>
                        <th className="border border-line p-2 text-left text-[10px] font-semibold text-muted w-32">Status</th>
                        <th className="border border-line p-2 text-left text-[10px] font-semibold text-muted">Cum este în text</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {claimsForCorrections.map(claim => (
                        <tr key={claim.id} className="bg-surface hover:bg-accent-soft/30 transition-colors">
                          <td className="border border-line p-2 font-mono text-[10px] text-ink">{claim.code}</td>
                          <td className="border border-line p-2 text-[11px] text-gray-700 dark:text-gray-300 break-words">{claim.raw_note}</td>
                          <td className="border border-line p-2">{statusChip(claim.status)}</td>
                          <td className="border border-line p-2 text-[11px] text-ink break-words whitespace-pre-wrap">
                            {claim.correction || claim.statement}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* ── Remaining sections ── */}
            {detail && detail.sections.length > 0 && (
              <section className="card p-3 sm:p-4 space-y-2">
                <h4 className="font-semibold text-sm text-ink">Mai multe secțiuni</h4>
                <div className="space-y-3">
                  {detail.sections.map(section => (
                    <div key={section.kind} className="space-y-1.5">
                      <h5 className="text-[11px] font-semibold uppercase tracking-wider text-muted">{section.title}</h5>
                      <div className="prose prose-sm dark:prose-invert max-w-none text-sm break-words">
                        {renderSectionBody(section.body_md)}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* ── Harta dosarului ── */}
            {detail && (
              <NoteMap
                scope="dossier"
                payload={mapPayload}
                dossierId={detail.dossier.id}
                onRefresh={refreshDetail}
              />
            )}

            {/* ── Round journal ── */}
            {detail && detail.rounds.length > 0 && (
              <section className="card p-3 sm:p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4 text-muted shrink-0" />
                  <h4 className="font-semibold text-sm text-ink">Jurnalul verificărilor</h4>
                </div>
                <div className="space-y-2">
                  {detail.rounds.map(round => (
                    <div key={round.id} className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-300">
                      <div className="w-1.5 h-1.5 rounded-full bg-chrono-600 shrink-0 mt-1" />
                      <div className="min-w-0">
                        <p className="text-sm text-ink leading-snug break-words">{round.summary}</p>
                        <span className="text-[10px] text-muted block">{formatDate(round.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
