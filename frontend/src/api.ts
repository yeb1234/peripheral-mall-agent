import type {
  ChatMeta,
  ConfirmResult,
  Driver,
  OrderPublic,
  Product,
  ResourceItem,
  VerifyResult,
} from './types'

const ENV = ((import.meta as any).env || {}) as Record<string, any>

/**
 * 生产（后端直接挂载 dist）默认同源相对路径，避免用 127.0.0.1 / 局域网 IP / 域名
 * 打开页面时被 CORS 拦死；vite dev（5173）仍直连 8000，由后端 CORS 放行。
 */
const BASE: string = ENV.VITE_API_BASE || (ENV.DEV ? 'http://localhost:8000' : '')

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<T>
}

export function apiBase() {
  return BASE
}

/** 把后端静态资源路径拼成完整 URL（dev 走 8000，prod 由后端同源提供）。 */
export function staticUrl(path?: string | null): string {
  if (!path) return ''
  if (path.startsWith('http')) return path
  return apiBase() + path
}

/** 按品类拿到对应的 SVG 插画。 */
export function categoryImage(category: string): string {
  return apiBase() + `/static/products/${category}.svg`
}

// ---------------- 商品 ----------------
export async function getProducts(params?: {
  category?: string
  q?: string
  featured?: boolean
}): Promise<Product[]> {
  const q = new URLSearchParams()
  if (params?.category) q.set('category', params.category)
  if (params?.q) q.set('q', params.q)
  if (params?.featured !== undefined) q.set('featured', String(params.featured))
  const r = await fetch(`${BASE}/api/products?${q.toString()}`)
  return json<Product[]>(r)
}

export async function getProduct(ident: string): Promise<Product> {
  const r = await fetch(`${BASE}/api/products/${ident}`)
  return json<Product>(r)
}

/** 分类列表直接来自后端 DB，前端不再硬编码「有哪些品类」。 */
export async function getCategories(): Promise<{ key: string; label: string }[]> {
  const r = await fetch(`${BASE}/api/products/categories`)
  return json<{ key: string; label: string }[]>(r)
}

export async function getDrivers(): Promise<Driver[]> {
  const r = await fetch(`${BASE}/api/drivers`)
  return json<Driver[]>(r)
}

export interface ResourceQuery {
  keyword?: string
  category?: string
  kind?: string
}

/** 资源下载中心：驱动 + 说明书 + 资源统一检索。 */
export async function getResources(params?: ResourceQuery): Promise<ResourceItem[]> {
  const q = new URLSearchParams()
  if (params?.keyword) q.set('keyword', params.keyword)
  if (params?.category) q.set('category', params.category)
  if (params?.kind) q.set('kind', params.kind)
  const r = await fetch(`${BASE}/api/resources?${q.toString()}`)
  return json<ResourceItem[]>(r)
}

// ---------------- 抢购 / 预售排队 ----------------
export interface FlashStatus {
  ok: boolean
  sku?: string
  position?: number | null
  won?: boolean
  stock_left?: number
  queue_len?: number
  expires_at?: number | null
  launch_at?: number | null
  total?: number
  reason?: string
}

export async function flashStatus(sku: string, buyerId: string): Promise<FlashStatus> {
  const r = await fetch(`${BASE}/api/flash-sale/status?sku=${encodeURIComponent(sku)}&buyer_id=${encodeURIComponent(buyerId)}`)
  return json<FlashStatus>(r)
}

export async function flashReserve(sku: string, buyerId: string): Promise<any> {
  const r = await fetch(`${BASE}/api/flash-sale/reserve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sku, buyer_id: buyerId }),
  })
  return json<any>(r)
}

export async function flashConfigure(sku: string, total: number, launchAt: number | null): Promise<any> {
  const r = await fetch(`${BASE}/api/flash-sale/configure`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sku, total_stock: total, launch_at: launchAt }),
  })
  return json<any>(r)
}

// ---------------- 订单 ----------------
export async function createOrder(payload: {
  buyer_id: string
  lines: { product_id?: string; sku?: string; qty: number }[]
  address: {
    province: string
    city: string
    district: string
    detail: string
    contact: string
    phone: string
  }
}): Promise<OrderPublic> {
  const token = await ensureBuyerToken(payload.buyer_id)
  const r = await fetch(`${BASE}/api/orders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, token }),
  })
  return json<OrderPublic>(r)
}

export async function getMyOrders(buyerId: string): Promise<OrderPublic[]> {
  const token = await ensureBuyerToken(buyerId)
  const q = `buyer_id=${encodeURIComponent(buyerId)}${token ? `&token=${encodeURIComponent(token)}` : ''}`
  const r = await fetch(`${BASE}/api/orders/mine?${q}`)
  return json<OrderPublic[]>(r)
}

// ---------------- 客服 Agent ----------------
const TOKEN_TTL_MS = 50 * 60 * 1000

/**
 * 买家身份签名：首次调用 `/api/auth/buyer` 换 token，本地按 buyer 缓存。
 * 目的是不让前端直接「裸传 buyer_id」——后端校验签名后才认这个身份。
 */
export async function ensureBuyerToken(buyerId: string): Promise<string | null> {
  if (typeof localStorage === 'undefined' || !buyerId) return null
  const key = `p4_token_${buyerId}`
  const saved = localStorage.getItem(key)
  const savedAt = Number(localStorage.getItem(`${key}_at`) || 0)
  if (saved && Date.now() - savedAt < TOKEN_TTL_MS) return saved
  try {
    const resp = await fetch(`${BASE}/api/auth/buyer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ buyer_id: buyerId }),
    })
    if (!resp.ok) return null
    const data = await resp.json() as { token?: string }
    if (data.token) {
      localStorage.setItem(key, data.token)
      localStorage.setItem(`${key}_at`, String(Date.now()))
      return data.token
    }
  } catch {
    // 签发失败就退回未签名模式（dev 后端允许；prod 会直接 401）
  }
  return null
}

export interface ChatError {
  code?: string
  message: string
}

export interface ChatHandlers {
  onToken: (ch: string) => void
  onMeta: (meta: ChatMeta) => void
  onError: (err: ChatError) => void
  /** 后端限流器让本次请求排队时回调（position = 前面还有几个请求） */
  onQueue?: (position: number) => void
}

export interface ChatPayload {
  session_id?: string | null
  buyer_id: string
  channel: string
  message: string
  /** 买家身份签名：后端据此确定 buyer_id，body 里的值只作展示 */
  token?: string | null
  /** 前端生成的请求 ID，便于排障与去重（后端原样回显在审计里） */
  request_id?: string
}

/**
 * 调用 SSE 流式对话接口。EventSource 只支持 GET，这里用 fetch + 手动解析 SSE，
 * 以便用 POST 传递会话上下文并消费 token / meta 事件。
 */
export async function chatSSE(
  payload: ChatPayload,
  handlers: ChatHandlers,
): Promise<void> {
  try {
    const resp = await fetch(`${BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!resp.ok) {
      const hint = resp.status === 429 ? '请求过于频繁，请稍后再试。'
        : resp.status === 401 ? '身份凭证无效，请刷新页面重试。'
        : `服务返回 HTTP ${resp.status}，请稍后重试。`
      handlers.onError({ code: `HTTP${resp.status}`, message: hint })
      return
    }
    if (!resp.body) {
      handlers.onError({ code: 'NET001', message: '无响应流' })
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    // sse-starlette 默认用 \r\n\r\n 分隔事件；旧版按 \n\n 切会导致事件全部堆积、气泡空白。
    // 用正则同时兼容 CRLF 与 LF，并在流结束后冲刷残留尾块。
    const consume = (block: string) => {
      let event = 'message'
      let data = ''
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) {
          // SSE 规范：data: 后最多去掉一个前导空格。不能用 trim()，否则空格字符 token 会被吞掉。
          const part = line.slice(5).replace(/^ /, '')
          data += (data ? '\n' : '') + part
        }
      }
      if (event === 'token') handlers.onToken(data)
      else if (event === 'meta') {
        try {
          handlers.onMeta(JSON.parse(data) as ChatMeta)
        } catch {
          /* ignore malformed meta */
        }
      } else if (event === 'error') {
        try {
          const parsed = JSON.parse(data) as { code?: string; message?: string }
          handlers.onError({ code: parsed.code, message: parsed.message || data })
        } catch {
          handlers.onError({ message: data })
        }
      } else if (event === 'queue') {
        try {
          const parsed = JSON.parse(data) as { position?: number }
          handlers.onQueue?.(parsed.position ?? 0)
        } catch {
          handlers.onQueue?.(0)
        }
      }
    }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      for (const block of blocks) consume(block)
    }
    if (buffer.trim()) consume(buffer) // 冲刷最后一块（服务端不一定以空行结尾）
  } catch (e: any) {
    handlers.onError({ code: 'NET000', message: e?.message || '网络错误' })
  }
}

export async function verifySession(sessionId: string, phoneLast4: string): Promise<VerifyResult> {
  const r = await fetch(`${BASE}/api/chat/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, phone_last4: phoneLast4 }),
  })
  return json<VerifyResult>(r)
}

export async function confirmAction(
  sessionId: string,
  confirmId: string,
  approve: boolean,
): Promise<ConfirmResult> {
  const r = await fetch(`${BASE}/api/chat/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, confirm_id: confirmId, approve }),
  })
  return json<ConfirmResult>(r)
}

// ---------------- 客服工作台 ----------------
export interface WBSession {
  session_id: string; buyer_id: string; channel: string
  created_at: string | null; last_message: string; bound_orders: number
}
export interface WBMessage {
  role: string; content: string; meta: string; created_at: string | null
}
export interface WBAudit {
  id: string; session_id: string; order_id: string
  event_type: string; actor: string; decision: string; created_at: string | null
}

export async function wbSessions(): Promise<WBSession[]> {
  const r = await fetch(`${BASE}/api/workbench/sessions`)
  return json<WBSession[]>(r)
}
export async function wbMessages(sessionId: string): Promise<WBMessage[]> {
  const r = await fetch(`${BASE}/api/workbench/sessions/${sessionId}/messages`)
  return json<WBMessage[]>(r)
}
export async function wbAudits(sessionId?: string): Promise<WBAudit[]> {
  const u = sessionId
    ? `${BASE}/api/workbench/audits?session_id=${encodeURIComponent(sessionId)}`
    : `${BASE}/api/workbench/audits`
  const r = await fetch(u)
  return json<WBAudit[]>(r)
}

// ---------------- LLM 设置：本地 / 外部 / Mock 运行时切换 ----------------
export type LLMProviderId = 'local' | 'external' | 'mock'

export interface LLMProviderCfg {
  label: string
  base_url: string
  api_key: string   // 后端返回脱敏值，原样回传即可（后端会保留真实 key）
  model: string
  has_key: boolean
}

export interface LLMProviders {
  active: LLMProviderId
  providers: Record<LLMProviderId, LLMProviderCfg>
  config_path: string
  rate_limit: Record<string, any>
}

export interface LocalModel {
  id: string
  kind: 'chat' | 'embedding' | 'reranker'
  chat: boolean
}

export interface LLMModelsResult {
  ok: boolean
  base_url: string
  count: number
  models: LocalModel[]
  detail?: string
}

export interface LLMTestResult {
  ok: boolean
  provider: string
  model?: string | null
  base_url?: string | null
  latency_ms?: number | null
  handshake?: string | null
  detail?: string | null
}

export interface LLMConfigPatch {
  label?: string
  base_url?: string
  api_key?: string
  model?: string
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return json<T>(r)
}

export function llmProviders(): Promise<LLMProviders> {
  return fetch(`${BASE}/api/llm/providers`).then((r) => json<LLMProviders>(r))
}

export function llmModels(baseUrl: string): Promise<LLMModelsResult> {
  return fetch(`${BASE}/api/llm/models?base_url=${encodeURIComponent(baseUrl)}`)
    .then((r) => json<LLMModelsResult>(r))
}

export function llmSaveConfig(payload: {
  active?: LLMProviderId
  local?: LLMConfigPatch
  external?: LLMConfigPatch
  mock?: LLMConfigPatch
}): Promise<LLMProviders & { ok: boolean; detail?: string }> {
  return postJSON('/api/llm/config', payload)
}

export function llmTest(payload: {
  provider: LLMProviderId
  base_url?: string
  api_key?: string
  model?: string
}): Promise<LLMTestResult> {
  return postJSON('/api/llm/test', payload)
}

export function llmPing(): Promise<any> {
  return fetch(`${BASE}/api/llm/ping`).then((r) => json<any>(r))
}

// ---------------- RAG 商品知识库 / SiliconFlow Reranker ----------------
export interface RagEmbedStatus {
  enabled: boolean; configured: boolean; has_key: boolean
  api_key: string; base_url: string
  embed_model: string; dim: number | null
  cached_docs: number; available: boolean
  total_calls?: number; total_success?: number; total_degraded?: number
  total_cached?: number; total_tokens?: number; avg_latency_ms?: number | null
  last_code?: string | null; last_detail?: string | null
}

export interface RagRetrieval {
  requested_mode: string
  effective_mode: string
  degraded: boolean
  code: string | null
  detail: string | null
  embed_model: string
  embed_latency_ms: number | null
  cached: number
  latency_ms: number | null
  kw_top: { sku: string; section_title?: string; rank: number; score: number }[]
  vec_top: { sku: string; section_title?: string; rank: number; score: number }[]
  kw_confidence?: number | null
  w_keyword?: number | null
  w_vector?: number | null
  fusion?: string | null
}

export interface RagStatus {
  ok: boolean
  embedding?: RagEmbedStatus
  enabled: boolean
  configured: boolean
  has_key: boolean
  api_key: string          // 后端脱敏
  base_url: string
  rerank_model: string
  embed_model: string
  recall_k: number
  top_k: number
  cache_ttl: number
  available: boolean
  catalog_size?: number
  pipeline?: string
  total_calls?: number
  total_success?: number
  total_degraded?: number
  total_cached?: number
  total_retries?: number
  avg_latency_ms?: number | null
  last_code?: string | null
  last_detail?: string | null
  detail?: string
}

export interface RagCandidate {
  sku: string; name: string; category_cn: string; price: number
  recall_rank: number; recall_score: number
  kw_rank?: number | null; vec_rank?: number | null; recall_source?: string
}

export interface RagHit {
  sku: string; name: string; category_cn: string; price: number
  rank: number; score: number | null
  recall_rank: number; recall_score: number; moved: number; text: string
}

export interface RagSearchResult {
  ok: boolean
  query?: string
  catalog_size?: number
  recall_k?: number
  top_k?: number
  degraded?: boolean
  code?: string | null
  detail?: string | null
  rerank?: {
    ok: boolean | null; degraded: boolean | null; code: string | null
    detail: string | null; model: string; cached: boolean | null
    latency_ms: number | null
  }
  candidates: RagCandidate[]
  results: RagHit[]
  retrieval?: RagRetrieval
  observation?: string
}

export interface RagRerankHit {
  index: number
  score: number | null
  text: string
}

export interface RagRerankResult {
  ok: boolean
  degraded: boolean
  code: string | null
  detail: string | null
  model: string
  cached: boolean
  latency_ms: number | null
  results: RagRerankHit[]
}

export function ragStatus(): Promise<RagStatus> {
  return fetch(`${BASE}/api/rag/status`).then((r) => json<RagStatus>(r))
}

export function ragSearch(
  query: string, topK = 3, useRerank = true, recallMode = 'hybrid',
): Promise<RagSearchResult> {
  return postJSON('/api/rag/search', {
    query, top_k: topK, use_rerank: useRerank, recall_mode: recallMode,
  })
}

export function ragRerank(query: string, documents: string[], topN = 3): Promise<RagRerankResult> {
  return postJSON('/api/rag/rerank', { query, documents, top_n: topN })
}

// ---------------- 说明书知识库：自动入库 / 章节检索 / 幂等同步 ----------------
export interface KnowledgeIndex {
  name: string
  count: number
  usage: string
}

export interface KnowledgeStatus {
  ok: boolean
  total_docs: number
  manual_docs: number
  resource_docs: number
  sku_count: number
  product_docs: number
  last_synced_at: string | null
  pipeline: string
  indexes: KnowledgeIndex[]
  code?: string
  detail?: string
}

export interface KnowledgeHit {
  sku: string
  product_name: string
  doc_title: string
  doc_kind: string
  source_kind: string
  section_no: number
  section_title: string
  content: string
  file_url: string
  rank: number
  score: number | null
  recall_rank: number
  recall_score: number
  moved: number
}

export interface KnowledgeCandidate {
  sku: string
  section_title: string
  recall_rank: number
  recall_score: number
}

export interface KnowledgeSearchResult {
  ok: boolean
  query: string
  sku: string | null
  doc_count: number
  recall_k: number
  top_k: number
  degraded: boolean
  code: string | null
  detail: string | null
  rerank?: {
    ok: boolean | null
    degraded: boolean | null
    code: string | null
    detail: string | null
    model: string | null
    cached: boolean | null
    latency_ms: number | null
  }
  candidates: KnowledgeCandidate[]
  results: KnowledgeHit[]
  observation?: string
}

export interface KnowledgeSyncResult {
  ok: boolean
  sources: number
  created: number
  updated: number
  unchanged: number
  removed: number
  chunks: number
  detail: string
  code?: string
}

export function knowledgeStatus(): Promise<KnowledgeStatus> {
  return fetch(`${BASE}/api/knowledge/status`).then((r) => json<KnowledgeStatus>(r))
}

export function knowledgeSearch(payload: {
  query: string
  product_sku?: string
  top_k?: number
  use_rerank?: boolean
}): Promise<KnowledgeSearchResult> {
  return postJSON('/api/knowledge/search', payload)
}

export function knowledgeSync(): Promise<KnowledgeSyncResult> {
  return postJSON('/api/knowledge/sync', {})
}

// ---------------- 联网搜索：阿里云百炼 EnhancedSearch MCP ----------------
export interface WebSearchHit {
  title: string
  url: string
  hostname: string
  snippet: string
}

export interface WebSearchStatus {
  ok: boolean
  enabled: boolean
  configured: boolean
  has_key: boolean
  api_key: string
  mcp_url: string
  tool: string
  max_results: number
  available: boolean
  pipeline?: string
  total_calls?: number
  total_success?: number
  total_degraded?: number
  total_cached?: number
  cached_results?: number
  last_latency_ms?: number | null
  avg_latency_ms?: number | null
  last_code?: string | null
  last_detail?: string | null
}

export interface WebSearchResult {
  ok: boolean
  degraded: boolean
  code: string | null
  detail: string | null
  query: string
  results: WebSearchHit[]
  cached: boolean
  latency_ms: number | null
  observation?: string
}

export function websearchStatus(): Promise<WebSearchStatus> {
  return fetch(`${BASE}/api/websearch/status`).then((r) => json<WebSearchStatus>(r))
}

export function websearchSearch(payload: {
  query: string
  category?: string
  time_range?: string
  top_k?: number
}): Promise<WebSearchResult> {
  return postJSON('/api/websearch/search', payload)
}

// ---------------- P4 产品资源库（静态资产 + MCP 供出） ----------------
export interface AssetCategory {
  key: string
  name: string
  visual: string
  count: number
}

export interface AssetSummary {
  sku: string
  name: string
  category: string
  category_cn: string
  brand: string
  price: number
  featured: boolean
  tagline: string
  file: string
  product_card: string
  category_visual: string
}

export interface AssetManifest {
  name: string
  version: string
  generated_at: string
  description: string
  asset_base_url: string
  product_count: number
  brand: {
    name: string
    slogan: string
    tone: string
    palette: Record<string, string>
    keywords: string[]
  }
  categories: AssetCategory[]
  mcp: {
    protocol: string
    http: string
    http_standalone: string
    stdio: string
    tools: string[]
  }
  products: AssetSummary[]
}

export interface AssetSellingPoint {
  key: string
  label: string
  fact: string
  headline: string
  benefit: string
  copy: string
}

export interface AssetShot {
  no: number
  timecode: string
  visual: string
  overlay_text: string
  voiceover: string
  asset_ref: string
}

export interface AssetBundle {
  schema_version: string
  sku: string
  identity: AssetSummary & { currency: string; stock: number; description: string }
  specs: Record<string, unknown>
  specs_flat: Record<string, string>
  copy: {
    tagline: string
    lead: string
    short_copy: string
    long_copy: string
    selling_points: AssetSellingPoint[]
    scenes: { scene: string; angle: string }[]
    video_script: {
      title: string
      duration_seconds: number
      aspect_ratio: string
      style: string
      brand_tone: string
      shots: AssetShot[]
      bgm_hint: string
      cta: string
      compliance_note: string
    }
    keywords: string[]
  }
  images: Record<string, {
    path: string
    url: string
    mime_type: string
    width: number
    height: number
    purpose: string
    note?: string
  }>
  downloads: { kind: string; title: string; file_url: string; sha256: string }[]
  provenance: Record<string, string>
}

export function assetUrl(path: string) {
  return `${BASE}/p4-assets/${path}`
}

export function getAssetManifest(): Promise<AssetManifest> {
  return fetch(`${BASE}/p4-assets/manifest.json`).then((r) => json<AssetManifest>(r))
}

export function getAssetBundle(sku: string): Promise<AssetBundle> {
  return fetch(`${BASE}/p4-assets/products/${encodeURIComponent(sku)}.json`)
    .then((r) => json<AssetBundle>(r))
}

// ---------------- 记忆系统：画像 / 习惯 / 购买记录 / 行为流 / 多任务续点 ----------------
export interface MemoryEventView {
  id: string
  kind: string
  sku: string
  summary: string
  payload: Record<string, any>
  task_id: string
  created_at: string | null
}

export interface AgentTaskView {
  id: string
  buyer_id: string
  session_id: string
  stage: string
  stage_cn: string
  title: string
  focus_sku: string
  status: 'active' | 'paused' | 'done'
  checkpoint: Record<string, any>
  turns: number
  last_message: string
  created_at: string | null
  updated_at: string | null
}

export interface MemoryDialogView {
  role: string
  content: string
  meta: Record<string, any>
  created_at: string | null
}

export interface MemorySnapshot {
  buyer_id: string
  session_id: string | null
  profile: {
    display_name: string
    prefs: Record<string, string>
    stats: Record<string, any>
    created_at: string | null
    updated_at: string | null
  }
  purchase: {
    orders: number
    valid_orders: number
    total_spent: number
    recent_skus: string[]
  }
  orders: any[]
  events: MemoryEventView[]
  dialog: MemoryDialogView[]
  tasks: AgentTaskView[]
  suggested_resume: AgentTaskView | null
  context: string
}

export interface MemoryResumeResult {
  ok: boolean
  task?: AgentTaskView
  suggested_message?: string
  reason?: string
}

/** 埋点默认带上当前浏览器身份，页面里不用到处传 buyerId。 */
function localBuyer(): string {
  try {
    return localStorage.getItem('p4_buyer') || 'buyer_demo'
  } catch {
    return 'buyer_demo'
  }
}

export function memorySnapshot(buyerId: string, sessionId?: string | null): Promise<MemorySnapshot> {
  const q = new URLSearchParams({ buyer_id: buyerId })
  if (sessionId) q.set('session_id', sessionId)
  return fetch(`${BASE}/api/memory/snapshot?${q.toString()}`).then((r) => json<MemorySnapshot>(r))
}

export function memoryEvent(
  kind: string,
  sku = '',
  summary = '',
  payload: Record<string, any> = {},
): Promise<any> {
  // 埋点失败绝不影响页面主流程
  return postJSON('/api/memory/event', {
    buyer_id: localBuyer(), kind, sku, summary, payload,
  }).catch(() => ({ ok: false }))
}

export function memoryEvents(buyerId: string, limit = 30): Promise<{ ok: boolean; events: MemoryEventView[] }> {
  return fetch(`${BASE}/api/memory/events?buyer_id=${encodeURIComponent(buyerId)}&limit=${limit}`)
    .then((r) => json<{ ok: boolean; events: MemoryEventView[] }>(r))
}

export function memoryTasks(
  buyerId: string,
  includeDone = false,
): Promise<{ ok: boolean; tasks: AgentTaskView[] }> {
  return fetch(`${BASE}/api/memory/tasks?buyer_id=${encodeURIComponent(buyerId)}&include_done=${includeDone}`)
    .then((r) => json<{ ok: boolean; tasks: AgentTaskView[] }>(r))
}

export function memoryResume(taskId: string): Promise<MemoryResumeResult> {
  return postJSON(`/api/memory/tasks/${encodeURIComponent(taskId)}/resume`, {})
}

export function memorySetProfile(
  buyerId: string,
  displayName?: string,
  prefs?: Record<string, string>,
): Promise<any> {
  return postJSON('/api/memory/profile', {
    buyer_id: buyerId, display_name: displayName, prefs,
  })
}

// ---------------- 第一层语义理解（NLU 实验台） ----------------
export interface NluAgent {
  order: number
  intent: string
  agent: string
  goal: string
  primary: boolean
}

export interface NluConfig {
  ok: boolean
  mode: string
  max_sub_intents: number
  provider: string
  modes: Record<string, string>
}

export interface NluResult {
  ok: boolean
  input: string
  nlu: {
    rewritten: string
    intent: string
    confidence: number
    slots: Record<string, string>
    sub_intents: Array<{ intent: string; goal: string; rewritten?: string }>
    needs_clarify: boolean
    reason: string
  }
  source: 'llm' | 'rule' | 'fallback'
  mode: string
  llm_called: boolean
  error_code: string | null
  injected: boolean
  guard: string | null
  context_chars: number
  history_turns: number
  queue: Array<Record<string, any>>
  agents: NluAgent[]
  elapsed_ms: number
  config: NluConfig
}

export function nluConfig(): Promise<NluConfig> {
  return fetch(`${BASE}/api/nlu/config`).then((r) => json<NluConfig>(r))
}

export function nluSetMode(mode: string): Promise<NluConfig> {
  return postJSON('/api/nlu/config', { mode })
}

export function nluAnalyze(text: string, buyerId: string, force = false): Promise<NluResult> {
  return postJSON('/api/nlu/analyze', { text, buyer_id: buyerId, force })
}
