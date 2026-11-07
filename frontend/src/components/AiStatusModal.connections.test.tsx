import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AiStatusModal } from './AiStatusModal'

vi.mock('./Toast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }) }))

const { aiMock } = vi.hoisted(() => ({
  aiMock: {
    status: vi.fn(),
    models: vi.fn(),
    providers: vi.fn(),
    setProvider: vi.fn(),
    setModel: vi.fn(),
    custom: vi.fn(),
    customModels: vi.fn(),
    saveCustom: vi.fn(),
    openCode: vi.fn(),
    saveOpenCode: vi.fn(),
    openCodeConsole: vi.fn(),
    saveOpenCodeConsole: vi.fn(),
    openCodeGo: vi.fn(),
    saveOpenCodeGo: vi.fn(),
  },
}))

vi.mock('../lib/api', () => ({ api: { ai: aiMock } }))

const providerEntries = [
  { id: 'deepseek', label: 'DeepSeek', configured: true, model: 'deepseek-chat' },
  { id: 'openai', label: 'OpenAI', configured: true, model: 'gpt-4o-mini' },
  { id: 'openrouter', label: 'OpenRouter', configured: false, model: 'anthropic/claude-3.5-sonnet' },
  { id: 'groq', label: 'Groq', configured: false, model: 'llama-3.3-70b-versatile' },
  { id: 'mistral', label: 'Mistral', configured: false, model: 'mistral-large-latest' },
  { id: 'together', label: 'Together', configured: false, model: 'meta-llama/Llama-3.3-70B-Instruct-Turbo' },
  { id: 'custom', label: 'Personalizat', configured: false, model: '' },
  { id: 'opencode_console', label: 'OpenCode Console', configured: true, model: 'console/text-small' },
  { id: 'opencode_go', label: 'OpenCode Go', configured: false, model: '' },
  { id: 'ollama', label: 'Ollama (local)', configured: true, model: 'llama3.2' },
  { id: 'opencode', label: 'OpenCode (nativ)', configured: true, model: 'opencode/model' },
  { id: 'mock', label: 'Mock (offline)', configured: true, model: 'mock-model' },
]

function providersResponse(active = 'deepseek') {
  return {
    providers: providerEntries.map(p => ({ ...p, active: p.id === active })),
    active,
    order: ['deepseek', 'custom'],
    configured: ['deepseek', 'opencode_console', 'ollama', 'opencode', 'mock'],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  aiMock.status.mockResolvedValue({
    provider_mode: 'auto',
    deepseek_model: 'deepseek-chat',
    ollama_model: 'llama3.2',
    configured_embed_model: 'embed-model',
    active_embed_model: null,
    semantic_retrieval: false,
    embedding_model_installed: true,
    embedding_dimension: 1024,
    available_embed_models: [],
    pdf_chunks_indexed: 0,
  })
  aiMock.models.mockResolvedValue({
    models: [{ id: 'gpt-4o', name: 'GPT-4o', context_window: 128000, input_modalities: ['text'] }],
    active: 'gpt-4o-mini',
    from_provider: true,
    active_listed: false,
  })
  aiMock.providers.mockResolvedValue(providersResponse())
  aiMock.setProvider.mockImplementation(async (provider: string) => ({ active: provider }))
  aiMock.setModel.mockImplementation(async (model: string) => ({ active: model, cleared: false }))
  aiMock.custom.mockResolvedValue({ configured: false, has_key: false })
  aiMock.customModels.mockResolvedValue({ url: 'http://127.0.0.1:8080/v1/models', models: [], count: 0 })
  aiMock.saveCustom.mockResolvedValue({ saved: true, base_url: 'http://127.0.0.1:8080/v1', label: 'Personalizat', model: 'local-model', active: 'deepseek' })
  aiMock.openCode.mockResolvedValue({ configured: true, base_url: 'http://127.0.0.1:4096', directory: '/tmp/project', has_password: true })
  aiMock.saveOpenCode.mockResolvedValue({ configured: true, base_url: 'http://127.0.0.1:4096', directory: '/tmp/project' })
  aiMock.openCodeConsole.mockResolvedValue({ configured: true, base_url: 'https://opencode.ai/inference', has_key: true })
  aiMock.saveOpenCodeConsole.mockResolvedValue({ configured: true, base_url: 'https://opencode.ai/inference', has_key: true })
  aiMock.openCodeGo.mockResolvedValue({ configured: false, base_url: 'https://opencode.ai/inference/go', has_key: false })
  aiMock.saveOpenCodeGo.mockResolvedValue({ configured: true, base_url: 'https://opencode.ai/inference/go', has_key: true })
})

describe('AI provider hub', () => {
  it('lists every provider with independent configured and active status', async () => {
    render(<AiStatusModal onClose={vi.fn()} />)

    const hub = await screen.findByRole('group', { name: 'Toți furnizorii AI' })
    for (const provider of providerEntries.filter(p => p.id !== 'opencode_console' && p.id !== 'opencode_go' && p.id !== 'opencode')) {
      expect(within(hub).getByText(provider.label)).toBeInTheDocument()
    }
    expect(within(hub).getByText('OpenCode')).toBeInTheDocument()
    expect(within(hub).queryByText('OpenCode Console')).not.toBeInTheDocument()
    expect(within(hub).queryByText('OpenCode (nativ)')).not.toBeInTheDocument()
    expect(hub).toBeInTheDocument()
    expect(screen.getByText(/Ordinea de rezervă: deepseek → custom/)).toBeInTheDocument()
  })

  it('keeps per-provider model selection separate and does not activate on settings selection', async () => {
    render(<AiStatusModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: /OpenAI/ }))
    expect(await screen.findByText(/se gestionează în/)).toHaveTextContent('OPENAI_API_KEY')
    expect(screen.getByRole('textbox', { name: 'Model OpenAI' })).toHaveValue('gpt-4o-mini')
    expect(aiMock.setProvider).not.toHaveBeenCalled()
    expect(aiMock.models).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Citește modelele' }))
    await waitFor(() => expect(aiMock.models).toHaveBeenCalledWith('openai'))
    fireEvent.change(await screen.findByRole('combobox', { name: 'Model OpenAI' }), { target: { value: 'gpt-4o' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvează modelul' }))

    await waitFor(() => expect(aiMock.setModel).toHaveBeenCalledWith('gpt-4o', 'openai'))
    expect(aiMock.setProvider).not.toHaveBeenCalled()
  })

  it('groups OpenCode in one card while keeping Console and local settings separate', async () => {
    render(<AiStatusModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))
    expect(await screen.findByRole('region', { name: 'Setări Console direct' })).toBeInTheDocument()
    expect(screen.getByLabelText('Cheie API OpenCode Console')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Setări OpenCode local' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Server local/ }))
    expect(await screen.findByRole('region', { name: 'Setări OpenCode local' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Setări Console direct' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('URL server OpenCode')).toBeInTheDocument()
  })

  it('saves and tests Console without activating it or sending a completion', async () => {
    render(<AiStatusModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))
    expect(await screen.findByRole('combobox', { name: 'Alege modelul OpenCode Console' })).toBeInTheDocument()
    expect(screen.getByText(/Big Pickle și Beam Free.*clientul oficial OpenCode/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Salvează și testează conexiunea' })).toBeEnabled())
    fireEvent.change(screen.getByLabelText('Cheie API OpenCode Console'), { target: { value: 'test-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvează și testează conexiunea' }))

    await waitFor(() => expect(aiMock.saveOpenCodeConsole).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'test-key' })))
    await waitFor(() => expect(aiMock.models).toHaveBeenCalledWith('opencode_console'))
    expect(aiMock.setProvider).not.toHaveBeenCalled()
    expect(aiMock.setModel).not.toHaveBeenCalled()
  })

  it('tests a saved Console connection on opening and immediately exposes its model selector', async () => {
    aiMock.providers.mockResolvedValue(providersResponse('opencode_console'))
    aiMock.models.mockResolvedValue({
      models: [
        { id: 'console/text-small', name: 'Text Small', context_window: 32000, input_modalities: ['text'] },
      ],
      active: 'console/text-small',
      from_provider: true,
      active_listed: true,
      active_supported: true,
    })

    render(<AiStatusModal onClose={vi.fn()} />)

    const modelSelect = await screen.findByRole('combobox', { name: 'Alege modelul OpenCode Console' })
    expect(modelSelect).toHaveValue('console/text-small')
    expect(within(modelSelect).getByRole('option', { name: /Text Small/ })).toBeInTheDocument()
    expect(aiMock.models).toHaveBeenCalledWith('opencode_console')
    expect(screen.getByText(/Conexiune reușită: 1 modele text disponibile/)).toBeInTheDocument()
    const testButton = screen.getByRole('button', { name: 'Testează conexiunea' })
    await waitFor(() => expect(testButton).toBeEnabled())
    expect(aiMock.saveOpenCodeConsole).not.toHaveBeenCalled()
    expect(aiMock.setProvider).not.toHaveBeenCalled()

    fireEvent.click(testButton)
    await waitFor(() => expect(aiMock.models).toHaveBeenCalledTimes(2))
  })

  it('shows Console test failures and permits an explicit retry without activating it', async () => {
    aiMock.providers.mockResolvedValue(providersResponse('opencode_console'))
    aiMock.models
      .mockRejectedValueOnce(new Error('HTTP 401: Unauthorized'))
      .mockResolvedValueOnce({
        models: [{ id: 'console/text-small', name: 'Text Small', context_window: 32000, input_modalities: ['text'] }],
        active: 'console/text-small',
        from_provider: true,
        active_listed: true,
        active_supported: true,
      })

    render(<AiStatusModal onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))

    expect(await screen.findByRole('status')).toHaveTextContent('HTTP 401: Unauthorized')
    expect(screen.getByRole('textbox', { name: 'Alege modelul OpenCode Console' })).toHaveValue('console/text-small')
    const testButton = screen.getByRole('button', { name: 'Testează conexiunea' })
    expect(testButton).toBeEnabled()
    fireEvent.click(testButton)

    const modelSelect = await screen.findByRole('combobox', { name: 'Alege modelul OpenCode Console' })
    expect(modelSelect).toHaveValue('console/text-small')
    expect(within(modelSelect).getByRole('option', { name: /Text Small/ })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Conexiune reușită: 1 modele text disponibile.')
    expect(aiMock.setProvider).not.toHaveBeenCalled()
    expect(aiMock.setModel).not.toHaveBeenCalled()
  })

  it('blocks a saved free Console model and explains how to choose a supported route', async () => {
    const response = providersResponse('deepseek')
    response.providers = response.providers.map(provider => provider.id === 'opencode_console'
      ? { ...provider, model: 'big-pickle' }
      : provider)
    aiMock.providers.mockResolvedValue(response)
    aiMock.models.mockResolvedValue({
      models: [{ id: 'console/text-small', name: 'Text Small', context_window: 32000, input_modalities: ['text'] }],
      active: 'big-pickle',
      from_provider: true,
      active_listed: false,
      active_supported: false,
      note: 'Modelul gratuit Big Pickle este disponibil numai prin clientul oficial OpenCode.',
    })

    render(<AiStatusModal onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))

    const consoleSettings = await screen.findByRole('region', { name: 'Setări Console direct' })
    expect(await within(consoleSettings).findByRole('status')).toHaveTextContent(/Modelul gratuit Big Pickle/)
    const modelSelect = await screen.findByRole('combobox', { name: 'Alege modelul OpenCode Console' })
    expect(modelSelect).toHaveValue('big-pickle')
    expect(within(modelSelect).getByRole('option', { name: /indisponibil prin API direct/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Activează Console direct' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Salvează modelul Console' })).toBeDisabled()
    expect(within(modelSelect).queryByRole('option', { name: /Beam Free/ })).not.toBeInTheDocument()
    expect(aiMock.setProvider).not.toHaveBeenCalled()
    expect(aiMock.setModel).not.toHaveBeenCalled()
  })

  it('shows the native OpenCode model control immediately and keeps a saved choice selected', async () => {
    aiMock.models.mockImplementation(async (provider?: string) => provider === 'opencode'
      ? {
          models: [
            { id: 'openai::gpt-test', name: 'GPT test', context_window: 32000, input_modalities: ['text'] },
            { id: 'anthropic::claude-test', name: 'Claude test', context_window: 64000, input_modalities: ['text'] },
          ],
          active: 'openai::gpt-test',
          from_provider: true,
          active_listed: true,
        }
      : {
          models: [{ id: 'gpt-4o', name: 'GPT-4o', context_window: 128000, input_modalities: ['text'] }],
          active: 'gpt-4o-mini',
          from_provider: true,
          active_listed: false,
        })
    aiMock.setModel.mockImplementation(async (model: string) => ({ active: model, cleared: false }))
    render(<AiStatusModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))
    fireEvent.click(screen.getByRole('button', { name: /Server local/ }))

    const modelSelect = await screen.findByRole('combobox', { name: 'Alege modelul OpenCode' })
    expect(modelSelect).toHaveValue('opencode/model')
    expect(aiMock.models).toHaveBeenCalledWith('opencode')
    const testButton = screen.getByRole('button', { name: 'Testează conexiunea' })
    await waitFor(() => expect(testButton).toBeEnabled())
    fireEvent.click(testButton)
    await waitFor(() => expect(aiMock.models.mock.calls.filter(([provider]) => provider === 'opencode')).toHaveLength(2))

    fireEvent.change(modelSelect, { target: { value: 'anthropic::claude-test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvează modelul OpenCode' }))

    await waitFor(() => expect(aiMock.setModel).toHaveBeenCalledWith('anthropic::claude-test', 'opencode'))
    await waitFor(() => expect(modelSelect).toHaveValue('anthropic::claude-test'))
    expect(screen.getByText(/Model salvat: anthropic::claude-test/)).toBeInTheDocument()
    expect(aiMock.setProvider).not.toHaveBeenCalled()
  })

  it('saves Custom settings without changing the active provider', async () => {
    render(<AiStatusModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: /Personalizat/ }))
    fireEvent.change(screen.getByLabelText('URL API furnizor personalizat'), { target: { value: 'http://127.0.0.1:8080/v1' } })
    fireEvent.change(screen.getByLabelText('Model (manual)'), { target: { value: 'local-model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvează configurația' }))

    await waitFor(() => expect(aiMock.saveCustom).toHaveBeenCalledWith(expect.objectContaining({
      base_url: 'http://127.0.0.1:8080/v1',
      model: 'local-model',
      activate: false,
    })))
    expect(aiMock.setProvider).not.toHaveBeenCalled()
  })

  it('activates only after the explicit provider activation button is pressed', async () => {
    render(<AiStatusModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))
    const activate = await screen.findByRole('button', { name: 'Activează Console direct' })
    expect(activate).toBeEnabled()
    fireEvent.click(activate)

    await waitFor(() => expect(aiMock.setProvider).toHaveBeenCalledWith('opencode_console'))
  })

  it('offers OpenCode Go as a separate subscription method with its included free models', async () => {
    const response = providersResponse('deepseek')
    response.providers = response.providers.map(provider => provider.id === 'opencode_go'
      ? { ...provider, configured: true, model: 'beam-free' }
      : provider)
    aiMock.providers.mockResolvedValue(response)
    aiMock.openCodeGo.mockResolvedValue({
      configured: true, base_url: 'https://opencode.ai/inference/go', has_key: true,
    })
    aiMock.models.mockImplementation(async (provider?: string) => provider === 'opencode_go'
      ? {
          models: [
            { id: 'deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash', context_window: 128000, input_modalities: ['text'] },
            { id: 'beam-free', name: 'Beam Free', context_window: 32000, input_modalities: ['text'], free: true },
          ],
          active: 'beam-free',
          from_provider: true,
          active_listed: true,
          active_supported: true,
          free_model_ids: ['beam-free'],
        }
      : { models: [], active: 'gpt-4o-mini', from_provider: false })

    render(<AiStatusModal onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /^OpenCode/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Go \(abonament\)/ }))

    const goSettings = await screen.findByRole('region', { name: 'Setări Go (abonament)' })
    expect(aiMock.openCodeGo).toHaveBeenCalled()
    const modelSelect = await within(goSettings).findByRole('combobox', { name: 'Alege modelul OpenCode Go' })
    expect(modelSelect).toHaveValue('beam-free')
    expect(within(modelSelect).getByRole('option', { name: /Beam Free.*gratuit/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Activează Go (abonament)' })).toBeEnabled()

    fireEvent.change(modelSelect, { target: { value: 'deepseek-v4.1-flash' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvează modelul Go' }))

    await waitFor(() => expect(aiMock.setModel).toHaveBeenCalledWith('deepseek-v4.1-flash', 'opencode_go'))
    expect(aiMock.setProvider).not.toHaveBeenCalled()
  })
})
