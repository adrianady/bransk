import { useEffect, useRef, useState } from 'react'
import { X, Cpu, CheckCircle2, XCircle, HelpCircle, Loader2 } from 'lucide-react'
import { api } from '../lib/api'
import type {
  AiModelsResponse,
  AiOpenCodeConfigState,
  AiOpenCodeConsoleConfigState,
  AiProviderInfo,
  AiProvidersResponse,
  AiStatusResponse,
} from '../lib/types'

const ENV_MANAGED_PROVIDERS = new Set(['deepseek', 'openai', 'openrouter', 'groq', 'mistral', 'together'])
const ENV_KEY_NAMES: Record<string, string> = {
  deepseek: 'DEEPSEEK_API_KEY',
  openai: 'OPENAI_API_KEY',
  openrouter: 'OPENROUTER_API_KEY',
  groq: 'GROQ_API_KEY',
  mistral: 'MISTRAL_API_KEY',
  together: 'TOGETHER_API_KEY',
}
const OPENCODE_HUB_ID = 'opencode_hub'

/** Console (pay-as-you-go) and Go (subscription) are separate services. */
type DirectService = 'console' | 'go'

const DIRECT_PROVIDERS: Record<DirectService, string> = {
  console: 'opencode_console',
  go: 'opencode_go',
}
const DIRECT_LABELS: Record<DirectService, string> = {
  console: 'OpenCode Console',
  go: 'OpenCode Go',
}
const DIRECT_DEFAULT_URLS: Record<DirectService, string> = {
  console: 'https://opencode.ai/inference',
  go: 'https://opencode.ai/inference/go',
}

type DirectServiceState = {
  url: string
  key: string
  config: AiOpenCodeConsoleConfigState | null
  models: AiModelsResponse | null
  modelId: string
  busy: boolean
  note: string
}

const blankDirect = (url: string): DirectServiceState => ({
  url, key: '', config: null, models: null, modelId: '', busy: false, note: '',
})

export function AiStatusModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<AiStatusResponse | null>(null)
  const [error, setError] = useState('')
  const [catalogue, setCatalogue] = useState<AiModelsResponse | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [modelNote, setModelNote] = useState('')
  const [providers, setProviders] = useState<AiProvidersResponse | null>(null)
  const [settingsProvider, setSettingsProvider] = useState('')
  const [providerModelDrafts, setProviderModelDrafts] = useState<Record<string, string>>({})
  const [catalogueProvider, setCatalogueProvider] = useState('')
  const [catalogueBusy, setCatalogueBusy] = useState(false)
  const [providerNote, setProviderNote] = useState('')
  // Custom endpoint form: URL + key, then "fetch models" to prove it works.
  const [customUrl, setCustomUrl] = useState('')
  const [customKey, setCustomKey] = useState('')
  const [customModel, setCustomModel] = useState('')
  const [customFetching, setCustomFetching] = useState(false)
  const [customSaving, setCustomSaving] = useState(false)
  const [customModels, setCustomModels] = useState<string[]>([])
  const [customNote, setCustomNote] = useState('')
  const [customHasKey, setCustomHasKey] = useState(false)
  const [ollamaModel, setOllamaModel] = useState('')
  const [openCodeState, setOpenCodeState] = useState<AiOpenCodeConfigState | null>(null)
  const [openCodeMethod, setOpenCodeMethod] = useState<DirectService | 'local' | null>(null)
  const [openCodeUrl, setOpenCodeUrl] = useState('http://127.0.0.1:4096')
  const [openCodeDirectory, setOpenCodeDirectory] = useState('')
  const [openCodeModel, setOpenCodeModel] = useState('')
  const [openCodeModels, setOpenCodeModels] = useState<AiModelsResponse | null>(null)
  const [openCodeBusy, setOpenCodeBusy] = useState(false)
  const [openCodeNote, setOpenCodeNote] = useState('')
  const [direct, setDirect] = useState<Record<DirectService, DirectServiceState>>({
    console: blankDirect(DIRECT_DEFAULT_URLS.console),
    go: blankDirect(DIRECT_DEFAULT_URLS.go),
  })
  const autoTestedOpenCode = useRef({ console: '', go: '', local: '' })

  const patchDirect = (service: DirectService, patch: Partial<DirectServiceState>) =>
    setDirect(current => ({ ...current, [service]: { ...current[service], ...patch } }))

  const loadCatalogue = async (provider?: string) => {
    const target = provider || settingsProvider || providers?.active || ''
    setCatalogueBusy(true)
    setCatalogueProvider(target)
    setCatalogue(null)
    setModelNote('')
    try {
      // Goes through request(): honours BASE/VITE_API_BASE_URL and has a timeout.
      const result = await api.ai.models(provider)
      setCatalogue(result)
      setProviderModelDrafts(current => Object.prototype.hasOwnProperty.call(current, target)
        ? current
        : { ...current, [target]: result.active })
    } catch (e) {
      setModelNote(e instanceof Error ? e.message : 'Lista de modele nu s-a putut citi.')
    } finally {
      setCatalogueBusy(false)
    }
  }

  const loadProviders = async () => {
    try {
      const data = await api.ai.providers()
      setProviders(data)
      setSettingsProvider(current => current
        || (data.active === 'opencode' || data.active === 'opencode_console' || data.active === 'opencode_go'
          ? OPENCODE_HUB_ID
          : data.active))
      setOpenCodeMethod(current => current ?? (
        data.active === 'opencode' ? 'local' : data.active === 'opencode_go' ? 'go' : 'console'
      ))
      setProviderModelDrafts(current => {
        const next = { ...current }
        for (const provider of data.providers) {
          if (!Object.prototype.hasOwnProperty.call(next, provider.id)) next[provider.id] = provider.model
        }
        return next
      })
      const savedOpenCodeModel = data.providers.find(p => p.id === 'opencode')?.model
      if (savedOpenCodeModel) setOpenCodeModel(current => current || savedOpenCodeModel)
      const savedOllamaModel = data.providers.find(p => p.id === 'ollama')?.model
      if (savedOllamaModel) setOllamaModel(savedOllamaModel)
      for (const service of ['console', 'go'] as DirectService[]) {
        const savedModel = data.providers.find(p => p.id === DIRECT_PROVIDERS[service])?.model
        if (savedModel) {
          setDirect(current => current[service].modelId
            ? current
            : { ...current, [service]: { ...current[service], modelId: savedModel } })
        }
      }
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
      await loadProviders()
    } catch (e) {
      setProviderNote(e instanceof Error ? e.message : 'Schimbarea furnizorului a eșuat.')
    } finally {
      setChoosing(false)
    }
  }

  const showProviderSettings = (providerId: string) => {
    if (providerId === OPENCODE_HUB_ID) {
      setSettingsProvider(OPENCODE_HUB_ID)
      setOpenCodeMethod(providers?.active === 'opencode'
        ? 'local'
        : providers?.active === 'opencode_go' ? 'go' : 'console')
      setCatalogue(null)
      setCatalogueProvider('')
      setModelNote('')
      return
    }
    setSettingsProvider(providerId)
    setCatalogue(null)
    setCatalogueProvider('')
    setModelNote('')
  }

  useEffect(() => {
    // Prefill the form with the saved endpoint, so editing does not mean retyping.
    api.ai.custom().then(c => {
      if (!c.configured) return
      setCustomUrl(c.base_url || '')
      setCustomModel(c.model || '')
      setCustomHasKey(Boolean(c.has_key))
    }).catch(() => { /* no custom provider yet */ })
  }, [])

  useEffect(() => {
    let isMounted = true
    Promise.all([
      api.ai.openCode().catch(() => null),
      api.ai.openCodeConsole().catch(() => null),
      api.ai.openCodeGo().catch(() => null),
    ]).then(([localConfig, consoleConfig, goConfig]) => {
      if (!isMounted) return
      if (localConfig) {
        setOpenCodeState(localConfig)
        if (localConfig.configured) {
          setOpenCodeUrl(localConfig.base_url || '')
          setOpenCodeDirectory(localConfig.directory || '')
        }
      }
      if (consoleConfig) {
        patchDirect('console', { config: consoleConfig, url: consoleConfig.base_url || DIRECT_DEFAULT_URLS.console })
      }
      if (goConfig) {
        patchDirect('go', { config: goConfig, url: goConfig.base_url || DIRECT_DEFAULT_URLS.go })
      }
    })
    return () => { isMounted = false }
  }, [])

  const connectOpenCode = async () => {
    setOpenCodeBusy(true)
    setOpenCodeNote('')
    try {
      const saved = await api.ai.saveOpenCode({
        base_url: openCodeUrl,
        directory: openCodeDirectory,
      })
      autoTestedOpenCode.current.local = `${saved.base_url}|${saved.directory}`
      setOpenCodeState(saved)
      const models = await api.ai.models('opencode')
      setOpenCodeModels(models)
      if (models.active) setOpenCodeModel(models.active)
      await loadProviders()
      setOpenCodeNote(models.models.length
        ? `Conexiune reușită: ${models.models.length} modele disponibile prin serverul local OpenCode.`
        : (models.note || 'Serverul a răspuns, dar nu a listat modele.'))
    } catch (e) {
      setOpenCodeNote(e instanceof Error ? e.message : 'Conectarea la OpenCode a eșuat.')
    } finally {
      setOpenCodeBusy(false)
    }
  }

  const testOpenCodeConnection = async () => {
    autoTestedOpenCode.current.local = `${openCodeState?.base_url}|${openCodeState?.directory}`
    setOpenCodeBusy(true)
    setOpenCodeNote('')
    try {
      const models = await api.ai.models('opencode')
      setOpenCodeModels(models)
      if (models.active) setOpenCodeModel(current => current || models.active)
      setOpenCodeNote(models.models.length
        ? `Conexiune reușită: ${models.models.length} modele disponibile prin serverul local OpenCode.`
        : (models.note || 'Conexiunea nu a returnat modele.'))
    } catch (e) {
      setOpenCodeModels(null)
      setOpenCodeNote(e instanceof Error ? e.message : 'Testarea conexiunii OpenCode a eșuat.')
    } finally {
      setOpenCodeBusy(false)
    }
  }

  const directApi = (service: DirectService) => service === 'go'
    ? { load: () => api.ai.openCodeGo(), save: api.ai.saveOpenCodeGo }
    : { load: () => api.ai.openCodeConsole(), save: api.ai.saveOpenCodeConsole }

  const readDirectModels = async (service: DirectService) => {
    const models = await api.ai.models(DIRECT_PROVIDERS[service])
    patchDirect(service, {
      models,
      ...(models.active && !direct[service].modelId ? { modelId: models.active } : {}),
    })
    const resultNote = models.models.length
      ? `Conexiune reușită: ${models.models.length} modele text disponibile.`
      : (models.note || 'Conexiunea nu a returnat modele.')
    patchDirect(service, {
      note: models.note && models.models.length ? `${resultNote} ${models.note}` : resultNote,
    })
    return models
  }

  const testDirectConnection = async (service: DirectService) => {
    autoTestedOpenCode.current[service] = direct[service].config?.base_url || `configured-${service}`
    patchDirect(service, { busy: true, note: '' })
    try {
      await readDirectModels(service)
    } catch (e) {
      patchDirect(service, {
        models: null,
        note: e instanceof Error ? e.message : `Testarea conexiunii ${DIRECT_LABELS[service]} a eșuat.`,
      })
    } finally {
      patchDirect(service, { busy: false })
    }
  }

  const saveAndTestDirect = async (service: DirectService) => {
    patchDirect(service, { busy: true, note: '' })
    try {
      const saved = await directApi(service).save({
        base_url: direct[service].url,
        ...(direct[service].key.trim() ? { api_key: direct[service].key } : {}),
      })
      autoTestedOpenCode.current[service] = saved.base_url || `configured-${service}`
      patchDirect(service, { config: saved, key: '' })
      await loadProviders()
      await readDirectModels(service)
    } catch (e) {
      patchDirect(service, {
        note: e instanceof Error ? e.message : `Salvarea sau testarea ${DIRECT_LABELS[service]} a eșuat.`,
      })
    } finally {
      patchDirect(service, { busy: false })
    }
  }

  const chooseDirectModel = async (service: DirectService, modelId: string) => {
    if (!modelId) {
      patchDirect(service, { modelId: '' })
      return
    }
    setChoosing(true)
    patchDirect(service, { note: '' })
    try {
      const result = await api.ai.setModel(modelId, DIRECT_PROVIDERS[service])
      patchDirect(service, {
        modelId: result.active,
        models: direct[service].models ? {
          ...direct[service].models,
          active: result.active,
          active_listed: true,
          active_supported: true,
          note: '',
        } : null,
      })
      await loadProviders()
      patchDirect(service, {
        note: `Model salvat: ${result.active}. Poți activa ${DIRECT_LABELS[service]}.`,
      })
    } catch (e) {
      patchDirect(service, { note: e instanceof Error ? e.message : 'Salvarea modelului a eșuat.' })
    } finally {
      setChoosing(false)
    }
  }

  const clearDirectKey = async (service: DirectService) => {
    patchDirect(service, { busy: true, note: '' })
    try {
      const saved = await directApi(service).save({ base_url: direct[service].url, clear_key: true })
      patchDirect(service, {
        config: saved, models: null, modelId: '',
        note: 'Cheia salvată a fost ștearsă din setările locale.',
      })
      await loadProviders()
    } catch (e) {
      patchDirect(service, { note: e instanceof Error ? e.message : 'Ștergerea cheii a eșuat.' })
    } finally {
      patchDirect(service, { busy: false })
    }
  }

  const chooseProviderModel = async (providerId: string, modelId: string) => {
    setChoosing(true)
    setModelNote('')
    try {
      const body = await api.ai.setModel(modelId, providerId)
      setProviderModelDrafts(current => ({ ...current, [providerId]: body.active }))
      setCatalogue(current => current ? { ...current, active: body.active } : current)
      await loadProviders()
      setModelNote(`Model salvat pentru ${providers?.providers.find(p => p.id === providerId)?.label ?? providerId}: ${body.active}. Providerul activ nu s-a schimbat.`)
    } catch (e) {
      setModelNote(e instanceof Error ? e.message : 'Salvarea modelului a eșuat.')
    } finally {
      setChoosing(false)
    }
  }

  const chooseOpenCodeModel = async (modelId: string) => {
    setChoosing(true)
    try {
      const result = await api.ai.setModel(modelId, 'opencode')
      setOpenCodeModel(result.active)
      setOpenCodeModels(current => current ? { ...current, active: result.active } : current)
      setOpenCodeNote(`Model salvat: ${result.active}. Providerul activ nu s-a schimbat.`)
      await loadProviders()
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
        base_url: customUrl, api_key: customKey, model: customModel, label: 'Personalizat', activate: false,
      })
      setCustomKey('')
      setCustomHasKey(Boolean(customKey.trim()) || customHasKey)
      setCustomNote(`Salvat pentru Personalizat. Model: ${res.model || '—'} prin ${res.base_url}. Providerul activ nu s-a schimbat.`)
      await loadProviders()
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
    loadProviders()
    return () => { isMounted = false }
  }, [])

  useEffect(() => {
    if (settingsProvider !== OPENCODE_HUB_ID) return

    if (openCodeMethod === 'console' || openCodeMethod === 'go') {
      const service = openCodeMethod
      const config = direct[service].config
      if (config?.configured && !direct[service].models && !direct[service].busy) {
        const identity = config.base_url || `configured-${service}`
        if (autoTestedOpenCode.current[service] !== identity) {
          autoTestedOpenCode.current[service] = identity
          void testDirectConnection(service)
        }
      }
    }

    if (openCodeMethod === 'local' && openCodeState?.configured && !openCodeModels && !openCodeBusy) {
      const identity = `${openCodeState.base_url}|${openCodeState.directory}`
      if (autoTestedOpenCode.current.local !== identity) {
        autoTestedOpenCode.current.local = identity
        void testOpenCodeConnection()
      }
    }
  }, [
    settingsProvider,
    openCodeMethod,
    direct,
    openCodeState?.configured,
    openCodeState?.base_url,
    openCodeState?.directory,
    openCodeModels,
    openCodeBusy,
  ])

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

  const directProviders: Record<DirectService, AiProviderInfo | undefined> = {
    console: providers?.providers.find(provider => provider.id === 'opencode_console'),
    go: providers?.providers.find(provider => provider.id === 'opencode_go'),
  }
  const openCodeLocalProvider = providers?.providers.find(provider => provider.id === 'opencode')
  const providerCards = providers?.providers.flatMap(provider => {
    if (provider.id === 'opencode' || provider.id === 'opencode_go') return []
    if (provider.id === 'opencode_console') {
      return [{
        id: OPENCODE_HUB_ID,
        label: 'OpenCode',
        configured: Boolean(directProviders.console?.configured || directProviders.go?.configured
          || openCodeLocalProvider?.configured),
        model: `Console: ${directProviders.console?.model || 'nesetat'} · Go: ${directProviders.go?.model || 'nesetat'} · local: ${openCodeLocalProvider?.model || 'nesetat'}`,
        active: Boolean(directProviders.console?.active || directProviders.go?.active
          || openCodeLocalProvider?.active),
      }]
    }
    return [provider]
  })

  const renderDirectPanel = (service: DirectService) => {
    const state = direct[service]
    const provider = directProviders[service]
    const label = DIRECT_LABELS[service]
    const isGo = service === 'go'
    const models = state.models
    const savedNotListed = Boolean(models?.models.length
      && state.modelId
      && !models.models.some(model => model.id === state.modelId))
    return (
      <div
        className="mt-3 space-y-2"
        role="region"
        aria-label={isGo ? 'Setări Go (abonament)' : 'Setări Console direct'}
      >
        <p className="text-[11px] text-gray-500 leading-relaxed">
          {isGo ? (
            <>
              Abonament <strong>Go / Go Plus / Go Teams</strong>: folosește cheia din{' '}
              <a href="https://opencode.ai/console" target="_blank" rel="noopener noreferrer" className="underline">OpenCode Console</a>.
              {' '}Consumul se scade din limita lunară a abonamentului, nu din creditele pay-as-you-go, iar modelele gratuite incluse (Beam Free) sunt permise și din clienți externi.
            </>
          ) : (
            <>
              Folosește o cheie personală sau de service din{' '}
              <a href="https://opencode.ai/console" target="_blank" rel="noopener noreferrer" className="underline">OpenCode Console</a>.
              {' '}Inferența cere API key; OAuth-ul Console este pentru MCP. Modelele Free-tier, inclusiv Big Pickle și Beam Free, sunt disponibile numai prin clientul oficial OpenCode. Pentru Bransk alege un model pay-as-you-go, metoda Go dacă ai abonament sau OpenCode local.
            </>
          )}
        </p>
        <label className="block text-[11px] text-gray-500">URL API
          <input
            value={state.url}
            onChange={e => patchDirect(service, { url: e.target.value })}
            placeholder={DIRECT_DEFAULT_URLS[service]}
            className="input w-full text-xs mt-0.5"
            aria-label={`URL API ${label}`}
            autoComplete="url"
          />
        </label>
        <p className="text-[10px] text-gray-500">
          {isGo
            ? <>Endpoint abonament: <code>/inference/go</code> · modelele listate sunt cele incluse în Go.</>
            : <>Pay-as-you-go: <code>/inference</code> · pentru abonament alege metoda Go.</>}
        </p>
        <label className="block text-[11px] text-gray-500">Cheie API
          <input
            value={state.key}
            onChange={e => patchDirect(service, { key: e.target.value })}
            type="password"
            placeholder={state.config?.has_key ? 'Cheie salvată — gol păstrează cheia' : 'oc_sk_…'}
            autoComplete="new-password"
            className="input w-full text-xs mt-0.5"
            aria-label={`Cheie API ${label}`}
          />
        </label>
        <p className="text-[10px] text-gray-500 leading-relaxed">
          Cheia se trimite doar backendului și rămâne în SQLite local, necriptată la repaus; nu este salvată în browser.
          {state.config?.has_key ? ' Cheie salvată.' : ' Nicio cheie salvată.'}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void saveAndTestDirect(service)}
            disabled={state.busy || !state.url.trim()}
            className="btn-secondary text-xs px-2 py-1"
          >
            {state.busy ? <><Loader2 className="w-3 h-3 animate-spin" /> Testez…</> : 'Salvează și testează conexiunea'}
          </button>
          <button
            type="button"
            onClick={() => void testDirectConnection(service)}
            disabled={state.busy || !state.config?.configured || Boolean(state.key.trim()) || state.url !== state.config?.base_url}
            className="btn-secondary text-xs px-2 py-1"
          >
            Testează conexiunea
          </button>
          <button
            type="button"
            onClick={() => void chooseProvider(DIRECT_PROVIDERS[service])}
            disabled={choosing || !provider?.configured || models?.active_supported === false}
            className="btn-primary text-xs px-2 py-1"
          >
            {choosing ? 'Activez…' : (isGo ? 'Activează Go (abonament)' : 'Activează Console direct')}
          </button>
          {state.config?.has_key && (
            <button
              type="button"
              onClick={() => void clearDirectKey(service)}
              disabled={state.busy}
              className="btn-secondary text-xs px-2 py-1"
            >
              Șterge cheia
            </button>
          )}
        </div>
        {models?.models.length ? (
          <label className="block text-[11px] text-gray-500">Model {isGo ? 'Go' : 'Console'}
            <select
              value={state.modelId}
              onChange={e => patchDirect(service, { modelId: e.target.value })}
              disabled={choosing}
              className="input w-full text-xs mt-1"
              aria-label={`Alege modelul ${label}`}
            >
              <option value="">Alege un model…</option>
              {savedNotListed && (
                <option value={state.modelId}>
                  {state.modelId}{models.active_supported === false && state.modelId === models.active
                    ? ' (salvat · gratuit; indisponibil prin API direct)'
                    : ' (salvat)'}
                </option>
              )}
              {models.models.map(model => (
                <option key={model.id} value={model.id}>
                  {model.name} · {model.id}{model.free ? ' · gratuit' : ''}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className="block text-[11px] text-gray-500">Model {isGo ? 'Go' : 'Console'}
            <input
              value={state.modelId}
              onChange={e => patchDirect(service, { modelId: e.target.value })}
              className="input mt-1 w-full text-xs"
              aria-label={`Alege modelul ${label}`}
            />
          </label>
        )}
        <button
          type="button"
          onClick={() => void chooseDirectModel(service, state.modelId)}
          disabled={choosing || !state.modelId.trim() || (models?.from_provider === true && savedNotListed)}
          className="btn-primary text-xs px-2 py-1"
        >
          Salvează modelul {isGo ? 'Go' : 'Console'}
        </button>
        {state.note && (
          <p role="status" className="text-[11px] text-gray-500 dark:text-gray-400 break-words">{state.note}</p>
        )}
      </div>
    )
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Furnizori AI și stare"
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Cpu className="w-5 h-5 text-chrono-700" /> Furnizori AI și stare
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

              {/* Every backend provider is discoverable here; opening its card
                  edits only that provider, while activation remains explicit. */}
              <div className="py-2 border-b border-gray-100 dark:border-gray-800">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-gray-500">Furnizor AI</span>
                  <span className="text-sm font-medium">
                    {providers?.providers.find(p => p.active)?.label ?? providers?.active ?? '—'}
                  </span>
                </div>
                {providers ? (
                  <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2" role="group" aria-label="Toți furnizorii AI">
                    {providerCards?.map(p => (
                      <button
                        type="button"
                        key={p.id}
                        onClick={() => showProviderSettings(p.id)}
                        aria-pressed={settingsProvider === p.id}
                        className={`rounded-lg border p-2.5 text-left transition-colors ${settingsProvider === p.id
                          ? 'border-chrono-500 bg-chrono-50 dark:border-chrono-600 dark:bg-chrono-950/30'
                          : 'border-gray-200 hover:border-chrono-300 dark:border-gray-700 dark:hover:border-chrono-700'}`}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold">{p.label}</span>
                          <span className="flex shrink-0 gap-1">
                            {p.active && <span className="badge badge-verdigris">Activ</span>}
                            <span className={`badge ${p.configured ? 'badge-verdigris' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
                              {p.configured ? 'Configurat' : 'Neconfigurat'}
                            </span>
                          </span>
                        </span>
                        <span className="mt-1 block truncate text-[10px] text-gray-500">Model: {p.model || 'nesetat'}</span>
                      </button>
                    ))}
                  </div>
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
                {providers && settingsProvider && (() => {
                  const selected = providers.providers.find(p => p.id === settingsProvider)
                  const isOpenCodeHub = settingsProvider === OPENCODE_HUB_ID
                  if (!selected && !isOpenCodeHub) return null
                  const selectedLabel = selected?.label ?? 'OpenCode'
                  const activeLabel = providers.providers.find(p => p.active)?.label
                  return (
                    <section className="mt-3 rounded-lg border border-gray-200 p-3 dark:border-gray-700" aria-label={`Setări ${selectedLabel}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{selectedLabel}</h3>
                          <p className="text-[11px] text-gray-500">{isOpenCodeHub ? `Metodă activă: ${activeLabel === 'OpenCode (nativ)' ? 'server local' : activeLabel === 'OpenCode Console' ? 'Console direct' : activeLabel === 'OpenCode Go' ? 'Go (abonament)' : 'niciuna'}.` : `Model: ${selected?.model || 'nesetat'} · setările sunt separate de providerul activ.`}</p>
                        </div>
                        {isOpenCodeHub ? (
                          activeLabel === 'OpenCode (nativ)' || activeLabel === 'OpenCode Console' || activeLabel === 'OpenCode Go'
                            ? <span className="badge badge-verdigris">OpenCode activ</span>
                            : <span className="badge bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">OpenCode inactiv</span>
                        ) : selected?.active ? (
                          <span className="badge badge-verdigris">Furnizor activ</span>
                        ) : (
                          <button type="button" onClick={() => selected && void chooseProvider(selected.id)} disabled={choosing || !selected?.configured} className="btn-primary text-xs px-2 py-1.5" aria-label={`Activează ${selectedLabel}`}>
                            {choosing ? 'Activez…' : `Activează ${selectedLabel}`}
                          </button>
                        )}
                      </div>
                      {isOpenCodeHub && (
                        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3" role="group" aria-label="Metode OpenCode">
                          <button type="button" aria-pressed={openCodeMethod === 'console'} onClick={() => setOpenCodeMethod('console')} className={`rounded-lg border p-2 text-left text-xs ${openCodeMethod === 'console' ? 'border-chrono-500 bg-chrono-50 dark:border-chrono-600 dark:bg-chrono-950/30' : 'border-gray-200 dark:border-gray-700'}`}>
                            Console direct
                            <span className="mt-0.5 block text-[10px] text-gray-500">{directProviders.console?.configured ? `Plată per cerere · ${directProviders.console.model || 'fără model'}` : 'Pay-as-you-go · necesită cheie'}</span>
                          </button>
                          <button type="button" aria-pressed={openCodeMethod === 'go'} onClick={() => setOpenCodeMethod('go')} className={`rounded-lg border p-2 text-left text-xs ${openCodeMethod === 'go' ? 'border-chrono-500 bg-chrono-50 dark:border-chrono-600 dark:bg-chrono-950/30' : 'border-gray-200 dark:border-gray-700'}`}>
                            Go (abonament)
                            <span className="mt-0.5 block text-[10px] text-gray-500">{directProviders.go?.configured ? `Abonament · ${directProviders.go.model || 'fără model'}` : 'Go / Go Plus · necesită cheie'}</span>
                          </button>
                          <button type="button" aria-pressed={openCodeMethod === 'local'} onClick={() => setOpenCodeMethod('local')} className={`rounded-lg border p-2 text-left text-xs ${openCodeMethod === 'local' ? 'border-chrono-500 bg-chrono-50 dark:border-chrono-600 dark:bg-chrono-950/30' : 'border-gray-200 dark:border-gray-700'}`}>
                            Server local
                            <span className="mt-0.5 block text-[10px] text-gray-500">{openCodeLocalProvider?.configured ? `Configurat · ${openCodeLocalProvider.model || 'model implicit'}` : 'Necesită opencode serve'}</span>
                          </button>
                        </div>
                      )}
                      {selected && ENV_MANAGED_PROVIDERS.has(selected.id) && (
                        <div className="mt-2 space-y-2">
                          <p className="text-[11px] text-gray-500">Cheia nu se afișează și se gestionează în <code>backend/.env</code> ca <code>{ENV_KEY_NAMES[selected.id]}</code>. {selected.configured ? 'Cheie detectată.' : 'Cheie nedetectată.'}</p>
                          <button type="button" onClick={() => void loadCatalogue(selected.id)} disabled={catalogueBusy} className="btn-secondary text-xs px-2 py-1">{catalogueBusy && catalogueProvider === selected.id ? 'Citesc…' : 'Citește modelele'}</button>
                          {catalogueProvider === selected.id && catalogue?.models.length ? (
                            <label className="block text-[11px] text-gray-500">Model pentru {selected.label}
                              <select value={providerModelDrafts[selected.id] ?? catalogue.active} onChange={e => setProviderModelDrafts(current => ({ ...current, [selected.id]: e.target.value }))} disabled={choosing} className="input mt-1 w-full text-xs" aria-label={`Model ${selected.label}`}>
                                {!catalogue.models.some(model => model.id === (providerModelDrafts[selected.id] ?? catalogue.active)) && <option value={providerModelDrafts[selected.id] ?? catalogue.active}>{providerModelDrafts[selected.id] ?? catalogue.active} (model salvat)</option>}
                                {catalogue.models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}
                              </select>
                            </label>
                          ) : (
                            <label className="block text-[11px] text-gray-500">Model pentru {selected.label}
                              <input value={providerModelDrafts[selected.id] ?? selected.model} onChange={e => setProviderModelDrafts(current => ({ ...current, [selected.id]: e.target.value }))} className="input mt-1 w-full text-xs" aria-label={`Model ${selected.label}`} />
                            </label>
                          )}
                          <div className="flex flex-wrap items-center gap-2">
                            {catalogueProvider === selected.id && catalogue?.models.length ? (
                              <button type="button" onClick={() => void chooseProviderModel(selected.id, providerModelDrafts[selected.id] ?? catalogue.active)} disabled={choosing || !(providerModelDrafts[selected.id] ?? catalogue.active).trim()} className="btn-primary text-xs px-2 py-1">Salvează modelul</button>
                            ) : (
                              <button type="button" onClick={() => void chooseProviderModel(selected.id, providerModelDrafts[selected.id] ?? selected.model)} disabled={choosing || !(providerModelDrafts[selected.id] ?? selected.model).trim()} className="btn-primary text-xs px-2 py-1">Salvează modelul</button>
                            )}
                            {catalogueProvider === selected.id && catalogue && !catalogue.models.length && <p className="text-[11px] text-gray-500">{catalogueBusy ? 'Se încarcă…' : catalogue.note || 'Lista nu este disponibilă; poți salva un model manual.'}</p>}
                            {catalogueBusy && catalogueProvider === selected.id && <p className="text-[11px] text-gray-500">Se citește catalogul…</p>}
                          </div>
                          {modelNote && <p role="status" className="text-[11px] text-chrono-600 dark:text-chrono-400">{modelNote}</p>}
                        </div>
                      )}
                      {selected?.id === 'ollama' && (
                        <div className="mt-2 space-y-2">
                          <p className="text-[11px] text-gray-500">Provider local. Scrie modelul instalat în Ollama; salvarea nu activează automat furnizorul.</p>
                          <label className="block text-[11px] text-gray-500">Model Ollama
                            <input value={ollamaModel} onChange={e => setOllamaModel(e.target.value)} className="input mt-1 w-full text-xs" aria-label="Model Ollama" />
                          </label>
                          <button type="button" onClick={() => void chooseProviderModel('ollama', ollamaModel)} disabled={choosing || !ollamaModel.trim()} className="btn-secondary text-xs px-2 py-1">Salvează modelul</button>
                          {modelNote && <p role="status" className="text-[11px] text-gray-500">{modelNote}</p>}
                        </div>
                      )}
                      {selected?.id === 'mock' && <p className="mt-2 text-[11px] text-gray-500">Provider offline pentru dezvoltare și teste; întoarce placeholder și nu trimite cereri externe.</p>}
                    </section>
                  )
                })()}
                {providers && (['console', 'go'] as DirectService[]).some(service =>
                  settingsProvider !== DIRECT_PROVIDERS[service]
                  && directProviders[service]?.configured
                  && providers.active !== DIRECT_PROVIDERS[service]) && (
                  <div role="status" className="mt-2 rounded-lg border border-brass/30 bg-brass-soft/50 p-2.5 text-xs text-gray-700 dark:text-gray-300">
                    <p className="font-medium">
                      Un serviciu OpenCode direct este configurat, dar furnizorul activ este{' '}
                      {providers.providers.find(p => p.id === providers.active)?.label ?? providers.active}.
                    </p>
                    <p className="mt-1">
                      Salvarea cheii și a modelului nu schimbă providerul conversațiilor. Deschide cardul OpenCode, alege metoda (Console sau Go) și apasă butonul de activare; nu intră automat în fallback.
                    </p>
                  </div>
                )}

                {settingsProvider === 'custom' && (
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
                    <label className="block text-[11px] text-gray-500">Cheie API (niciodată returnată de backend)
                      <input
                        value={customKey}
                        onChange={e => setCustomKey(e.target.value)}
                        type="password"
                        placeholder={customHasKey ? 'Cheie salvată — reintrodu-o pentru actualizare' : 'sk-…'}
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
                        disabled={customSaving || !customUrl.trim() || (customHasKey && !customKey.trim())}
                        className="btn-primary text-xs px-2 py-1"
                      >
                        {customSaving ? 'Salvez…' : 'Salvează configurația'}
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
                    {customHasKey && <p className="text-[10px] text-gray-500">Există o cheie în SQLite; reintrodu-o pentru a o trimite la salvare.</p>}
                  </div>
                )}

                {settingsProvider === OPENCODE_HUB_ID && (
                  <section className="mt-3 rounded-lg border border-gray-200 dark:border-gray-700 p-2.5">
                    <div className="mb-2">
                      <p className="text-sm font-semibold text-gray-700 dark:text-gray-200">{openCodeMethod === 'console' ? 'Conexiune Console direct' : openCodeMethod === 'go' ? 'Conexiune OpenCode Go (abonament)' : 'Conexiune OpenCode local'}</p>
                      <p className="text-[11px] text-gray-500">Sunt integrări distincte, cu protocoale și autentificare separate.</p>
                    </div>
                  {openCodeMethod === 'console' && renderDirectPanel('console')}
                  {openCodeMethod === 'go' && renderDirectPanel('go')}
                  {openCodeMethod === 'local' && (
                    <div className="mt-3 space-y-2" role="region" aria-label="Setări OpenCode local">
                      <p className="text-[11px] text-gray-500 leading-relaxed">
                        Conectează providerul în OpenCode prin <code>/connect</code>, pornește <code>opencode serve</code>, apoi indică serverul local.
                        Credentialele rămân în OpenCode; sunt acceptate doar localhost/loopback, iar permisiunile tool sunt refuzate. API-ul HTTP V2 este experimental.
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
                      <label className="block text-[11px] text-gray-500">Director proiect (cale absolută)
                        <input
                          value={openCodeDirectory}
                          onChange={e => setOpenCodeDirectory(e.target.value)}
                          placeholder="/home/utilizator/proiect"
                          className="input w-full text-xs mt-0.5"
                          aria-label="Director OpenCode"
                        />
                      </label>
                      <p className="text-[10px] text-gray-500 leading-relaxed">
                        Parola Basic Auth nu se salvează: setează <code>OPENCODE_SERVER_PASSWORD</code> și <code>OPENCODE_SERVER_URL</code> în backend; URL-ul trebuie să coincidă.
                        {openCodeState?.has_password ? ' Parola este disponibilă.' : ' Fără parolă în environment.'}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={connectOpenCode}
                          disabled={openCodeBusy || !openCodeUrl.trim() || !openCodeDirectory.trim()}
                          className="btn-secondary text-xs px-2 py-1"
                        >
                          {openCodeBusy ? <><Loader2 className="w-3 h-3 animate-spin" /> Testez…</> : 'Salvează și testează conexiunea'}
                        </button>
                        <button
                          type="button"
                          onClick={() => void testOpenCodeConnection()}
                          disabled={openCodeBusy || !openCodeState?.configured || openCodeUrl !== openCodeState?.base_url || openCodeDirectory !== openCodeState?.directory}
                          className="btn-secondary text-xs px-2 py-1"
                        >
                          Testează conexiunea
                        </button>
                        <button type="button" onClick={() => void chooseProvider('opencode')} disabled={choosing || !openCodeLocalProvider?.configured} className="btn-primary text-xs px-2 py-1">
                          {choosing ? 'Activez…' : 'Activează serverul local'}
                        </button>
                      </div>
                      {openCodeModels?.models.length ? (
                        <label className="block text-[11px] text-gray-500">Model OpenCode
                          <select
                            value={openCodeModel || openCodeModels.active}
                            onChange={e => setOpenCodeModel(e.target.value)}
                            disabled={choosing}
                            className="input w-full text-xs mt-1"
                            aria-label="Alege modelul OpenCode"
                          >
                            {openCodeModel && !openCodeModels.models.some(model => model.id === openCodeModel) && <option value={openCodeModel}>{openCodeModel} (salvat)</option>}
                            {openCodeModels.models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}
                          </select>
                        </label>
                      ) : (
                        <label className="block text-[11px] text-gray-500">Model OpenCode
                          <input value={openCodeModel} onChange={e => setOpenCodeModel(e.target.value)} className="input mt-1 w-full text-xs" aria-label="Alege modelul OpenCode" />
                        </label>
                      )}
                      <button type="button" onClick={() => void chooseOpenCodeModel(openCodeModel)} disabled={choosing || !openCodeModel.trim()} className="btn-primary text-xs px-2 py-1">Salvează modelul OpenCode</button>
                      {openCodeNote && <p role="status" className="text-[11px] text-gray-500 break-words">{openCodeNote}</p>}
                    </div>
                  )}
                  </section>
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
