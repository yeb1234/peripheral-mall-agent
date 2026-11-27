export interface Product {
  id: string
  sku: string
  name: string
  brand: string
  category: string
  price: number
  currency: string
  stock: number
  description: string
  specs: Record<string, any>
  image: string
  featured: boolean
  drivers?: Driver[]
}

export interface Driver {
  id: string
  name: string
  version: string
  os: string
  arch: string
  size_mb: number
  release_date: string
  changelog: string
  file_url: string
  sha256: string
  product_id: string
  product_sku: string
  product_name: string
  product_image: string
}

/** 资源下载中心的统一条目：驱动 / 说明书 / 资源（快速入门 / 保修卡等）。 */
export interface ResourceItem {
  id: string
  kind: 'driver' | 'manual' | 'resource'
  title: string
  size_mb: number
  release_date: string
  file_url: string
  sha256: string
  product_id: string
  product_sku: string
  product_name: string
  product_image: string
  brand: string
  category: string
  os?: string
  arch?: string
  version?: string
  lang?: string
  pages?: number
  resource_kind?: string
}

export interface OrderItem {
  name: string
  sku: string
  qty: number
  unit_price: number
}

export interface OrderPublic {
  order_no_tail: string
  status: string
  total: number
  items: OrderItem[]
  address_masked: string
  created_at: string | null
  carrier: string
  tracking_no: string
}

export interface CartLine {
  product: Product
  qty: number
}

/** 随 SSE meta 回传的「第一层理解」摘要，供客服面板展示系统如何理解这句话。 */
export interface ChatNlu {
  rewritten?: string
  intent?: string
  confidence?: number
  source?: string
  slots?: Record<string, string>
  sub_intents?: Array<{ intent: string; goal: string }>
  needs_clarify?: boolean
  reason?: string
  queue_len?: number
  segments?: number
  deferred?: Array<Record<string, any>>
  degraded_error?: string | null
}

export interface ChatMeta {
  session_id: string
  nlu?: ChatNlu
  intent?: string
  confirmation?: { confirm_id: string; preview: string; sensitive: boolean } | null
  need_bind?: boolean
  handoff?: boolean
  error_code?: string | null
  task_id?: string | null
  task_resumed?: boolean
  agent_trail?: AgentTransfer[]
}

/** 跨 Agent 交接记录：售前→售中→售后 的移交轨迹，来自后端 supervisor。 */
export interface AgentTransfer {
  seq: number
  from: string
  to: string
  goal?: string
}

export interface VerifyResult {
  ok: boolean
  bound?: number
  reason?: string
}

export interface ConfirmResult {
  ok: boolean
  observation: string
  handoff?: boolean
  data?: any
}
