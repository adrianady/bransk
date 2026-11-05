import { useEffect, useState } from 'react'
import { X, Cpu, CheckCircle2, XCircle, HelpCircle, Loader2 } from 'lucide-react'
import { api } from '../lib/api'
import type { AiModelsResponse, AiOpenCodeConfigState, AiProvidersResponse, AiStatusResponse } from '../lib/types'

export function AiStatusModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<AiStatusResponse | null>(null)
  const [error, setError] = useState('')
  const [catalogue, setCatalogue] = useState<AiModelsResponse | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [modelNote, setModelNote] = useState('')
  const [providers, setProviders] = useState<AiProvidersResponse | null>(null)
  const [providerNote, setProviderNote] = useState('')
  // Custom endpoint form: URL + key, then "fetch models" to prove it works.
  const [showCustom, setShowCustom] = useState(false)
  const [customUrl, setCustomUrl] = useState('')
  const [customKey, setCustomKey] = useState('')
  const [customModel, setCustomModel] = useState('')
  const [customFetching, setCustomFetching] = useState(false)
  const [customSaving, setCustomSaving] = useState(false)
  const [customModels, setCustomModels] = useState<string[]>([])
  const [customNote, setCustomNote] = useState('')
  const [showOpenCode, setShowOpenCode] = useState(false)
  const [openCodeState, setOpenCodeState] = useState<AiOpenCodeConfigState | null>(null)
  const [openCodeUrl, setOpenCodeUrl] = useState('http://127.0.0.1:4096')
  const [openCodeDirectory, setOpenCodeDirectory] = useState('')
  const [openCodeModels, setOpenCodeModels] = useState<AiModelsResponse | null>(null)
  const [openCodeBusy, setOpenCodeBusy] = useState(false)
  const [openCodeNote, setOpenCodeNote] = useState('')

  const loadCatalogue = async () => {
    try {
      // Goes through request(): honours BASE/VITE_API_BASE_URL and has a timeout.
      setCatalogue(await api.ai.models())
    } catch (e) {
      setModelNote(e instanceof Error ? e.message : 'Lista de modele nu s-a putut citi.')
    }
  }

  const loadProviders = async () => {
    try {
      setProviders(await api.ai.providers())
    } catch (e) {
      setProviderNote(e instanceof Error ? e.message : 'Lista de furnizori nu s-a putut citi.')
    }
  }

  const chooseProvider = async (providerId: string) => {
    setChoosing(true)
    setProviderNote('')
    try {
      const body = await api.ai.setProvider(providerId)
      const label = providers?.providers.find(p => p.id === body.active)?.label || body.active
      setProviderNote(`Furnizor activ: ${label}. Conversațiile următoare îl folosesc.`)
      await Promise.all([loadProviders(), loadCatalogue()])
    } catch (e) {
      setProviderNote(e instanceof Error ? e.message : 'Schimbarea furnizorului a eșuat.')
    } finally {
      setChoosing(false)
    }
  }

  useEffect(() => {
    // Prefill the form with the saved endpoint, so editing does not mean retyping.
    api.ai.custom().then(c => {
      if (!c.configured) return
      setCustomUrl(c.base_url || '')
      setCustomModel(c.model || '')
      setShowCustom(true)
    }).catch(() => { /* no custom provider yet */ })
  }, [])

  useEffect(() => {
    api.ai.openCode().then(config => {
      setOpenCodeState(config)
      if (config.configured) {
        setOpenCodeUrl(config.base_url || '')
        setOpenCodeDirectory(config.directory || '')
        setShowOpenCode(true)
      }
    }).catch(() => { /* optional provider may not be configured yet */ })
  }, [])

  const connectOpenCode = async () => {
    setOpenCodeBusy(true)
    setOpenCodeNote('')
    try {
      const saved = await api.ai.saveOpenCode({
        base_url: openCodeUrl,
        directory: openCodeDirectory,
      })
      setOpenCodeState(saved)
      const models = await api.ai.models('opencode')
      setOpenCodeModels(models)
      setOpenCodeNote(models.models.length
        ? `${models.models.length} modele disponibile prin API-ul nativ OpenCode.`
        : (models.note || 'Serverul a răspuns, dar nu a listat modele.'))
    } catch (e) {
      setOpenCodeNote(e instanceof Error ? e.message : 'Conectarea la OpenCode a eșuat.')
    } finally {
      setOpenCodeBusy(false)
    }
  }

  const activateOpenCode = async () => {
    setChoosing(true)
    try {
      await api.ai.setProvider('opencode')
      await Promise.all([loadProviders(), loadCatalogue()])
      setProviderNote('OpenCode nativ este activ pentru cererile următoare.')
    } catch (e) {
      setOpenCodeNote(e instanceof Error ? e.message : 'Nu s-a putut activa OpenCode.')
    } finally {
      setChoosing(false)
    }
  }

  const chooseOpenCodeModel = async (modelId: string) => {
    setChoosing(true)
    try {
      await api.ai.setProvider('opencode')
      const result = await api.ai.setModel(modelId)
      setOpenCodeNote(`Model activ: ${result.active}`)
      await Promise.all([loadProviders(), loadCatalogue()])
    } catch (e) {
      setOpenCodeNote(e instanceof Error ? e.message : 'Selectarea modelului a eșuat.')
    } finally {
      setChoosing(false)
    }
  }

  const fetchCustomModels = async () => {
    setCustomFetching(true); setCustomNote(''); setCustomModels([])
    try {
      const res = await api.ai.customModels({ base_url: customUrl, api_key: customKey })
      const ids = res.models.map(m => m.id)
      setCustomModels(ids)
      setCustomNote(ids.length
        ? `${ids.length} modele găsite la ${res.url}. Alege unul și salvează.`
        : `Endpointul răspunde, dar nu listează niciun model (${res.url}). Scrie numele modelului manual.`)
      if (ids.length === 1 && !customModel) setCustomModel(ids[0])
    } catch (e) {
      setCustomNote(e instanceof Error ? e.message : 'Cererea a eșuat.')
    } finally {
      setCustomFetching(false)
    }
  }

  const saveCustom = async () => {
    setCustomSaving(true); setCustomNote('')
    try {
      const res = await api.ai.saveCustom({
        base_url: customUrl, api_key: customKey, model: customModel, label: 'Personalizat', activate: true,
      })
      setCustomNote(`Salvat. Model în uz: ${res.model || '—'} prin ${res.base_url}.`)
      await Promise.all([loadProviders(), loadCatalogue()])
    } catch (e) {
      setCustomNote(e instanceof Error ? e.message : 'Salvarea a eșuat.')
    } finally {
      setCustomSaving(false)
    }
  }

  useEffect(() => {
    let isMounted = true
    api.ai.status()
      .then(s => { if (isMounted) setStatus(s) })
      .catch((e) => { if (isMounted) setError(e instanceof Error ? e.message : 'Starea AI nu e disponibilă') })
    loadCatalogue()
    loadProviders()
    return () => { isMounted = false }
  }, [])

  const chooseModel = async (modelId: string) => {
    setChoosing(true)
    setModelNote('')
    try {
      const body = await api.ai.setModel(modelId)
      setModelNote(`Model activ: ${body.active}`)
      // The status row and the menu must agree with what the server just did.
      setStatus(s => (s ? { ...s, deepseek_model: body.active } : s))
      await loadCatalogue()
    } catch (e) {
      setModelNote(e instanceof Error ? e.message : 'Schimbarea modelului a eșuat.')
    } finally {
      setChoosing(false)
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start justify-between gap-4 text-sm py-1.5 border-b border-gray-100 dark:border-gray-800 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className="text-right font-medium min-w-0 break-words">{value}</span>
    </div>
  )

  const yesNo = (ok: boolean) =>
    ok
      ? <span className="inline-flex items-center gap-1 text-verdigris"><CheckCircle2 className="w-4 h-4" /> da</span>
      : <span className="inline-flex items-center gap-1 text-madder"><XCircle className="w-4 h-4" /> nu</span>

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Cpu className="w-5 h-5 text-chrono-700" /> Starea AI
          </h2>
          <button onClick={onClose} className="icon-btn" aria-label="Închide fereastra" title="Închide (Esc)">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4">
          {error && (
            <p className="text-sm text-madder mb-3">⚠️ {error}</p>
          )}
          {!status && !error && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" /> Se încarcă starea…
            </div>
          )}
          {status && (
            <div>
              <Row label="Mod furnizor" value={status.provider_mode} />

              {/* The provider is a first-class choice: every OpenAI-compatible
                  service (OpenAI, OpenRouter, Groq, Mistral, Together) plus the
                  local Ollama. A provider without a key is listed, greyed, so a
                  missing key is visible instead of silently failing over. */}
              <div className="py-2 border-b border-gray-100 dark:border-gray-800">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-gray-500">Furnizor AI</span>
                  <span className="text-sm font-medium">
                    {providers?.providers.find(p => p.active)?.label ?? providers?.active ?? '—'}
                  </span>
                </div>
                {providers ? (
                  <select
                    value={providers.active}
                    onChange={e => chooseProvider(e.target.value)}
                    disabled={choosing}
                    className="input w-full text-sm mt-1.5"
                    aria-label="Alege furnizorul AI"
                  >
                    {providers.providers.map(p => (
                      <option key={p.id} value={p.id} disabled={!p.configured && p.id !== providers.active}>
                        {p.label}
                        {p.configured ? '' : ' — fără cheie'}
                        {p.id === 'ollama' ? ' (local)' : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-gray-400 mt-1">Se încarcă furnizorii…</p>
                )}
                {providers && (
                  <p className="text-[11px] text-gray-400 mt-1">
                    Ordinea de rezervă: {providers.order.join(' → ')}. Un furnizor fără cheie e sărit automat.
                  </p>
                )}
                {providerNote && (
                  <p className="text-xs text-chrono-600 dark:text-chrono-400 mt-1">{providerNote}</p>
                )}

                {/* A provider the user brings themselves: any OpenAI-compatible
                    endpoint (llama.cpp, LM Studio, a proxy). "Fetch models" is
                    the honest check that the URL and key work. */}
                <button
                  type="button"
                  onClick={() => setShowCustom(v => !v)}
                  className="text-xs text-chrono-600 dark:text-chrono-400 hover:underline mt-1.5"
                >
                  {showCustom ? 'Ascunde furnizorul personalizat' : '+ Adaugă un furnizor propriu (URL + cheie)'}
                </button>
                {showCustom && (
                  <div className="mt-2 space-y-1.5 rounded-lg border border-gray-200 dark:border-gray-700 p-2">
                    <label className="block text-[11px] text-gray-500">URL API (ex. http://127.0.0.1:8080/v1)
                      <input
                        value={customUrl}
                        onChange={e => setCustomUrl(e.target.value)}
                        placeholder="http://127.0.0.1:8080/v1"
                        className="input w-full text-xs mt-0.5"
                        aria-label="URL API furnizor personalizat"
                      />
                    </label>
                    <label className="block text-[11px] text-gray-500">Cheie API (lasă gol dacă serverul local nu cere)
                      <input
                        value={customKey}
                        onChange={e => setCustomKey(e.target.value)}
                        type="password"
                        placeholder="sk-…"
                        autoComplete="off"
                        className="input w-full text-xs mt-0.5"
                        aria-label="Cheie API furnizor personalizat"
                      />
                    </label>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={fetchCustomModels}
                        disabled={customFetching || !customUrl.trim()}
                        className="btn-secondary text-xs px-2 py-1"
                      >
                        {customFetching ? <><Loader2 className="w-3 h-3 animate-spin" /> Citesc…</> : 'Adu modelele'}
                      </button>
                      <button
                        type="button"
                        onClick={saveCustom}
                        disabled={customSaving || !customUrl.trim()}
                        className="btn-primary text-xs px-2 py-1"
                      >
                        {customSaving ? 'Salvez…' : 'Salvează și folosește'}
                      </button>
                    </div>
                    {customModels.length > 0 ? (
                      <select
                        value={customModel}
                        onChange={e => setCustomModel(e.target.value)}
                        className="input w-full text-xs"
                        aria-label="Modelul furnizorului personalizat"
                      >
                        {customModels.map(id => <option key={id} value={id}>{id}</option>)}
                      </select>
                    ) : (
                      <input
                        value={customModel}
                        onChange={e => setCustomModel(e.target.value)}
                        placeholder="numele modelului (dacă nu se listează)"
                        className="input w-full text-xs"
                        aria-label="Model (manual)"
                      />
                    )}
                    {customNote && (
                      <p className="text-[11px] text-gray-500 dark:text-gray-400 break-words">{customNote}</p>
                    )}
                  </div>
                )}

                <button
                  type="button"
                  onClick={() => setShowOpenCode(value => !value)}
                  className="block text-xs text-chrono-600 dark:text-chrono-400 hover:underline mt-2"
                >
                  {showOpenCode ? 'Ascunde OpenCode nativ' : '+ Configurează OpenCode nativ'}
                </button>
                {showOpenCode && (
                  <div className="mt-2 space-y-2 rounded-lg border border-gray-200 dark:border-gray-700 p-2.5">
                    <p className="text-[11px] text-gray-500 leading-relaxed">
                      API HTTP V2 experimental · sesiune temporară · toate permisiunile de tool sunt refuzate.
                      Conexiunea acceptă doar localhost/loopback.
                    </p>
                    <label className="block text-[11px] text-gray-500">URL server (fără /api)
                      <input
                        value={openCodeUrl}
                        onChange={e => setOpenCodeUrl(e.target.value)}
                        placeholder="http://127.0.0.1:4096"
                        className="input w-full text-xs mt-0.5"
                        aria-label="URL server OpenCode"
                      />
                    </label>
                    <label className="block text-[11px] text-gray-500">Directorul proiectului OpenCode (cale absolută)
                      <input
                        value={openCodeDirectory}
                        onChange={e => setOpenCodeDirectory(e.target.value)}
                        placeholder="/home/utilizator/proiect"
                        className="input w-full text-xs mt-0.5"
                        aria-label="Director OpenCode"
                      />
                    </label>
                    <p className="text-[11px] text-gray-500 leading-relaxed">
                      Parola nu se salvează în baza de date. Pentru un server protejat, setează
                      <code className="mx-1">OPENCODE_SERVER_PASSWORD</code> și
                      <code className="mx-1">OPENCODE_SERVER_URL</code> în environment-ul backend-ului; URL-ul trebuie să coincidă exact cu cel de mai sus.
                      {openCodeState?.has_password ? ' Parola este disponibilă în environment.' : ' Variabila de parolă nu este setată.'}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={connectOpenCode} disabled={openCodeBusy || !openCodeUrl.trim() || !openCodeDirectory.trim()} className="btn-secondary text-xs px-2 py-1">
                        {openCodeBusy ? <><Loader2 className="w-3 h-3 animate-spin" /> Verific…</> : 'Salvează și citește modelele'}
                      </button>
                      <button type="button" onClick={activateOpenCode} disabled={choosing || !openCodeState?.configured} className="btn-primary text-xs px-2 py-1">
                        {choosing ? 'Activez…' : 'Activează OpenCode'}
                      </button>
                    </div>
                    {openCodeModels?.models.length ? (
                      <label className="block text-[11px] text-gray-500">Model OpenCode
                        <select
                          value={openCodeModels.active}
                          onChange={e => void chooseOpenCodeModel(e.target.value)}
                          disabled={choosing}
                          className="input w-full text-xs mt-1"
                          aria-label="Alege modelul OpenCode"
                        >
                          {openCodeModels.models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}
                        </select>
                      </label>
                    ) : null}
                    {openCodeNote && <p className="text-[11px] text-gray-500 break-words">{openCodeNote}</p>}
                  </div>
                )}
              </div>

              {/* Choosing the model: the list comes from the provider, so it
                  shows what exists today, not what the code remembered. */}
              <div className="py-2 border-b border-gray-100 dark:border-gray-800">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-gray-500">Modelul furnizorului activ</span>
                  <span className="text-sm font-medium">{catalogue?.active ?? status.deepseek_model}</span>
                </div>
                {catalogue?.models.length ? (
                  <select
                    value={catalogue.active}
                    onChange={e => chooseModel(e.target.value)}
                    disabled={choosing}
                    className="input w-full text-sm mt-1.5"
                    aria-label="Alege modelul DeepSeek"
                  >
                    {/* The env default can be an alias the list does not show. */}
                    {!catalogue.active_listed && (
                      <option value={catalogue.active}>
                        {catalogue.active} (implicit din .env)
                      </option>
                    )}
                    {catalogue.models.map(m => (
                      <option key={m.id} value={m.id}>
                        {m.name} · {m.id}
                        {m.context_window ? ` · ${Math.round(m.context_window / 1000)}k context` : ''}
                        {m.input_modalities.includes('image') ? ' · vede imagini' : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-gray-400 mt-1">
                    {catalogue?.note || 'Lista de modele se încarcă…'}
                  </p>
                )}
                {modelNote && (
                  <p className="text-xs text-chrono-600 dark:text-chrono-400 mt-1">{modelNote}</p>
                )}
              </div>

              {status.embedding_backend !== "ollama" && (
                <Row label="Modelul Ollama (fallback, nefolosit)" value={status.ollama_model} />
              )}
              <Row label="Model de embedding (configurat)" value={status.configured_embed_model} />
              <Row
                label="Model de embedding (activ)"
                value={status.active_embed_model ?? '— niciunul —'}
              />
              <Row label="Căutare semantică" value={yesNo(status.semantic_retrieval)} />
              <Row label="Model de embedding instalat" value={yesNo(status.embedding_model_installed)} />
              <Row
                label="Dimensiunea embedding-ului"
                value={status.embedding_dimension ?? '—'}
              />
              <Row label="Fragmente PDF indexate" value={status.pdf_chunks_indexed} />

              {!status.embedding_model_installed && (
                <div className="mt-4 p-3 rounded-lg bg-brass-soft border border-brass/25 text-sm space-y-2">
                  <p className="flex items-start gap-2 text-brass">
                    <HelpCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    Căutarea semantică e oprită: serverul local de embeddings (llama.cpp, pe CPU) nu răspunde.
                  </p>
                  <code className="block text-xs bg-white dark:bg-gray-800 rounded px-2 py-1">
                    ./start-embeddings.sh
                  </code>
                  <p className="text-xs text-gray-600 dark:text-gray-400">
                    Îl pornești din folderul proiectului; modelul de embedding (bge-m3) se descarcă o singură
                    dată. Adresa serverului se schimbă cu <code>LLAMA_EMBED_URL</code> în
                    <code> backend/.env</code> — Ollama nu e necesar și nu e folosit când llama.cpp merge.
                  </p>
                </div>
              )}

              {status.available_embed_models.length > 0 && (
                <div className="mt-4">
                  <p className="text-xs font-medium text-gray-500 mb-1.5">Modele de embedding disponibile</p>
                  <div className="flex flex-wrap gap-1.5">
                    {status.available_embed_models.map(m => (
                      <span key={m} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{m}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
