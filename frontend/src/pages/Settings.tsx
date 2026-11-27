import { useEffect, useState } from 'react'
import {
  apiBase, knowledgeSearch, knowledgeStatus, knowledgeSync,
  llmModels, llmPing, llmProviders, llmSaveConfig, llmTest,
  ragRerank, ragSearch, ragStatus,
  websearchSearch, websearchStatus,
} from '../api'
import type {
  KnowledgeSearchResult,
  KnowledgeStatus,
  KnowledgeSyncResult,
  LLMProviderId,
  LLMProviders,
  LLMTestResult,
  LocalModel,
  RagRerankResult,
  RagSearchResult,
  RagStatus,
  WebSearchResult,
  WebSearchStatus,
} from '../api'

const META: Record<LLMProviderId, { title: string; sub: string; tag: string }> = {
  local: { title: '本地 LM Studio', sub: '零成本 · 无网络 · 无限速', tag: 'LOCAL' },
  external: { title: '外部 LLM', sub: 'DeepSeek V4.1 Flash（SenseNova）', tag: 'CLOUD' },
  mock: { title: 'Mock 离线', sub: '确定性规则 · 无密钥 · 适合学编排', tag: 'OFFLINE' },
}

interface FormState {
  base_url: string
  api_key: string
  model: string
}

/** 优先挑指令/代码类模型：thinking 模型与 VL 多模态在纯文本客服场景更慢更不稳。 */
function pickDefaultChat(models: LocalModel[]): string {
  const chat = models.filter((m) => m.chat)
  const preferred = chat.find((m) => /instruct|coder/i.test(m.id) && !/vl|vision/i.test(m.id))
  return (preferred || chat[0])?.id || ''
}

export default function Settings() {
  const [data, setData] = useState<LLMProviders | null>(null)
  const [tab, setTab] = useState<LLMProviderId>('local')
  const [form, setForm] = useState<FormState>({ base_url: '', api_key: '', model: '' })
  const [models, setModels] = useState<LocalModel[]>([])
  const [modelsMsg, setModelsMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState<LLMTestResult | null>(null)
  const [toast, setToast] = useState('')
  const [err, setErr] = useState('')
  const [ping, setPing] = useState<any>(null)

  // ---- RAG / Reranker 实验台 ----
  const [rag, setRag] = useState<RagStatus | null>(null)
  const [ragMsg, setRagMsg] = useState('')
  const [ragBusy, setRagBusy] = useState(false)
  const [ragQuery, setRagQuery] = useState('适合大手的无线轻量鼠标')
  const [ragResult, setRagResult] = useState<RagSearchResult | null>(null)
  const [recallMode, setRecallMode] = useState('hybrid')
  const [rerankQuery, setRerankQuery] = useState('适合大手的有线轻量化电竞鼠标')
  const [rerankResult, setRerankResult] = useState<RagRerankResult | null>(null)

  // ---- 说明书知识库：自动入库 / 章节问答 ----
  const [kb, setKb] = useState<KnowledgeStatus | null>(null)
  const [kbMsg, setKbMsg] = useState('')
  const [kbBusy, setKbBusy] = useState(false)
  const [kbQuery, setKbQuery] = useState('KB-PRO87 怎么配对蓝牙')
  const [kbSku, setKbSku] = useState('')
  const [kbResult, setKbResult] = useState<KnowledgeSearchResult | null>(null)
  const [kbSync, setKbSync] = useState<KnowledgeSyncResult | null>(null)

  // ---- 联网搜索：百炼 EnhancedSearch MCP ----
  const [ws, setWs] = useState<WebSearchStatus | null>(null)
  const [wsMsg, setWsMsg] = useState('')
  const [wsBusy, setWsBusy] = useState(false)
  const [wsQuery, setWsQuery] = useState('磁轴键盘是什么原理')
  const [wsResult, setWsResult] = useState<WebSearchResult | null>(null)

  const refreshRag = () => {
    ragStatus().then(setRag).catch((e: any) => setRagMsg(e?.message || '读取 RAG 状态失败'))
  }

  const refreshKb = () => {
    knowledgeStatus().then(setKb).catch((e: any) => setKbMsg(e?.message || '读取知识库状态失败'))
  }

  const refreshWs = () => {
    websearchStatus().then(setWs).catch((e: any) => setWsMsg(e?.message || '读取联网搜索状态失败'))
  }

  const WS_PRESETS = [
    '磁轴键盘是什么原理',
    '霍尔效应传感器是什么',
    '主动降噪耳机怎么选',
  ]

  const doWsSearch = async (q?: string) => {
    const query = (q ?? wsQuery).trim()
    if (!query) return
    setWsQuery(query)
    setWsBusy(true)
    setWsMsg('')
    try {
      setWsResult(await websearchSearch({ query }))
      refreshWs()
    } catch (e: any) {
      setWsMsg(e?.message || '联网搜索失败')
    } finally {
      setWsBusy(false)
    }
  }

  const notify = (s: string) => {
    setToast(s)
    window.setTimeout(() => setToast(''), 2800)
  }

  const syncForm = (d: LLMProviders, pid: LLMProviderId) => {
    const p = d.providers[pid]
    setForm({ base_url: p.base_url || '', api_key: p.api_key || '', model: p.model || '' })
  }

  const refreshModels = async (baseUrl: string, autoPick = false) => {
    if (!baseUrl) {
      setModelsMsg('请先填写 Base URL')
      return
    }
    setModelsMsg('正在读取本地模型列表…')
    try {
      const r = await llmModels(baseUrl)
      setModels(r.models)
      setModelsMsg(r.ok ? `发现 ${r.count} 个模型` : `探测失败：${r.detail}`)
      if (autoPick && r.ok) {
        const pick = pickDefaultChat(r.models)
        if (pick) setForm((f) => ({ ...f, model: f.model || pick }))
      }
    } catch (e: any) {
      setModelsMsg(`探测失败：${e?.message || e}`)
    }
  }

  useEffect(() => {
    ;(async () => {
      try {
        const d = await llmProviders()
        setData(d)
        setTab(d.active)
        syncForm(d, d.active)
        if (d.active === 'local') await refreshModels(d.providers.local.base_url, true)
      } catch (e: any) {
        setErr(e?.message || '加载配置失败')
      }
      refreshRag()
      refreshKb()
      refreshWs()
    })()
  }, [])

  const switchTab = (pid: LLMProviderId) => {
    if (!data) return
    setTab(pid)
    syncForm(data, pid)
    setTest(null)
    if (pid === 'local') void refreshModels(data.providers.local.base_url)
  }

  const applyResult = async (d: LLMProviders, message: string) => {
    setData(d)
    syncForm(d, tab)
    setTest(null)
    // 只有正在编辑本地页签时才刷新模型列表：否则 autoPick 会把外部 LLM 的
    // model 字段误填成本地模型名（setForm 不区分页签）。
    if (tab === 'local') {
      await refreshModels(d.providers.local.base_url, !d.providers.local.model)
    }
    notify(message)
  }

  const activate = async (pid: LLMProviderId) => {
    setBusy(true)
    setErr('')
    try {
      const d = await llmSaveConfig({ active: pid })
      setData(d)
      setTab(pid)
      syncForm(d, pid)
      setTest(null)
      if (pid === 'local') await refreshModels(d.providers.local.base_url, !d.providers.local.model)
      notify(`已启用「${META[pid].title}」，客服对话立即生效（无需重启）`)
    } catch (e: any) {
      setErr(e?.message || '切换失败')
    } finally {
      setBusy(false)
    }
  }

  const save = async (alsoActivate = false) => {
    setBusy(true)
    setErr('')
    try {
      const payload: any = {}
      payload[tab] = { ...form }
      if (alsoActivate) payload.active = tab
      const d = await llmSaveConfig(payload)
      await applyResult(d, alsoActivate ? '已保存并启用' : '配置已保存')
    } catch (e: any) {
      setErr(e?.message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  const doTest = async () => {
    setBusy(true)
    setTest(null)
    try {
      setTest(await llmTest({ provider: tab, ...form }))
    } catch (e: any) {
      setTest({ ok: false, provider: tab, detail: e?.message || String(e) })
    } finally {
      setBusy(false)
    }
  }

  const doPing = async () => {
    try {
      setPing(await llmPing())
    } catch (e: any) {
      setErr(e?.message || '自检失败')
    }
  }

  const RERANK_DOCS = [
    'VAXEE 有线鼠标，重量 58g，适合中大手趴握',
    '机械键盘 87 键，磁轴，铝合金上盖',
    '无线头戴耳机，支持 ANC 主动降噪',
  ]
  const PRESETS = [
    '适合大手的无线轻量鼠标',
    '带 ANC 主动降噪的无线耳机',
    '磁轴 铝合金 75% 键盘',
    'macOS 兼容的扩展坞',
  ]

  const doRerank = async () => {
    setRagBusy(true)
    setRagMsg('')
    try {
      setRerankResult(await ragRerank(rerankQuery, RERANK_DOCS, 3))
      refreshRag()
    } catch (e: any) {
      setRagMsg(e?.message || '重排请求失败')
    } finally {
      setRagBusy(false)
    }
  }

  const doRagSearch = async (q?: string, mode?: string) => {
    const query = (q ?? ragQuery).trim()
    if (!query) return
    setRagQuery(query)
    setRagBusy(true)
    setRagMsg('')
    try {
      setRagResult(await ragSearch(query, 3, true, mode ?? recallMode))
      refreshRag()
    } catch (e: any) {
      setRagMsg(e?.message || '检索请求失败')
    } finally {
      setRagBusy(false)
    }
  }

  const KB_PRESETS = [
    { q: 'KB-PRO87 怎么配对蓝牙', sku: 'KB-PRO87' },
    { q: '鼠标没反应怎么排查', sku: '' },
    { q: '键盘灯不亮怎么办', sku: '' },
    { q: 'DPI 和回报率怎么调', sku: '' },
    { q: '摄像头识别不到怎么排查', sku: '' },
  ]

  const doKbSearch = async (q?: string, sku?: string) => {
    const query = (q ?? kbQuery).trim()
    if (!query) return
    setKbQuery(query)
    if (sku !== undefined) setKbSku(sku)
    setKbBusy(true)
    setKbMsg('')
    try {
      setKbResult(await knowledgeSearch({
        query,
        product_sku: (sku ?? kbSku).trim(),
        top_k: 4,
        use_rerank: true,
      }))
      refreshKb()
    } catch (e: any) {
      setKbMsg(e?.message || '知识库检索失败')
    } finally {
      setKbBusy(false)
    }
  }

  const doKbSync = async () => {
    setKbBusy(true)
    setKbMsg('')
    try {
      setKbSync(await knowledgeSync())
      refreshKb()
    } catch (e: any) {
      setKbMsg(e?.message || '同步失败')
    } finally {
      setKbBusy(false)
    }
  }

  if (!data) {
    return <div className="settings-page">{err || '正在加载 LLM 配置…'}</div>
  }

  const active = data.active
  const rl = data.rate_limit || {}
  const patch = (k: keyof FormState, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <div className="settings-page">
      <div className="settings-head">
        <div>
          <div className="settings-kicker">SYSTEM / MODEL</div>
          <h1>LLM 提供方设置</h1>
          <p>本地 LM Studio、外部大模型、离线 Mock 随时切换；保存后客服对话立即生效，无需重启服务。</p>
        </div>
        <div className="settings-active">
          <span className="settings-live-dot" />
          当前生效：<strong>{META[active].title}</strong>
          <small>{data.providers[active].model || '—'}</small>
        </div>
      </div>

      <div className="settings-cards">
        {(Object.keys(META) as LLMProviderId[]).map((pid) => (
          <div
            key={pid}
            className={`settings-card ${active === pid ? 'active' : ''} ${tab === pid ? 'editing' : ''}`}
            onClick={() => switchTab(pid)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && switchTab(pid)}
          >
            <div className="settings-card-top">
              <span className="settings-tag">{META[pid].tag}</span>
              {active === pid && <span className="settings-badge">生效中</span>}
            </div>
            <h3>{META[pid].title}</h3>
            <p>{META[pid].sub}</p>
            <div className="settings-card-foot">
              <code>{data.providers[pid].model || '未设置模型'}</code>
              <span
                className={`settings-enable ${active === pid ? 'dim' : ''}`}
                onClick={(e) => {
                  e.stopPropagation()
                  if (active !== pid) void activate(pid)
                }}
              >
                {active === pid ? '已启用' : '启用 →'}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="settings-panel">
        <div className="settings-panel-head">
          <h2>编辑 · {META[tab].title}</h2>
          <span className="hint">密钥只存本机 llm_config.json，返回前端时脱敏</span>
        </div>

        {tab === 'mock' ? (
          <div className="settings-note">
            Mock 模式用内置关键词规则模拟「意图分类 → 工具选择 → 话术生成」，不调用任何模型。
            整套多 Agent 编排、六道关、二次确认、审计照常运行，适合离线慢慢读代码。
          </div>
        ) : (
          <div className="settings-fields">
            <label className="field">
              <span>Base URL</span>
              <input
                className="input"
                value={form.base_url}
                onChange={(e) => patch('base_url', e.target.value)}
                placeholder={tab === 'local' ? 'http://127.0.0.1:1234/v1' : 'https://token.sensenova.cn/v1'}
              />
            </label>

            {tab === 'local' && (
              <div className="field">
                <span>本地模型</span>
                <div className="settings-row">
                  <select
                    className="input"
                    value={form.model}
                    onChange={(e) => patch('model', e.target.value)}
                  >
                    <option value="">— 请选择可对话模型 —</option>
                    {models.map((m) => (
                      <option key={m.id} value={m.id} disabled={!m.chat}>
                        {m.id}
                        {m.kind === 'embedding' ? '  [嵌入模型 · 不能对话]' : ''}
                        {m.kind === 'reranker' ? '  [重排模型 · 不能对话]' : ''}
                        {/vl|vision/i.test(m.id) ? '  [多模态 · 纯文本客服可用]' : ''}
                      </option>
                    ))}
                  </select>
                  <button className="btn ghost sm" onClick={() => void refreshModels(form.base_url, true)}>
                    刷新模型
                  </button>
                </div>
                <small className={modelsMsg.startsWith('探测失败') ? 'settings-warn' : 'hint'}>
                  {modelsMsg || '点「刷新模型」读取 LM Studio 已加载的模型；需先在 LM Studio 里 Start Local Server'}
                </small>
              </div>
            )}

            {tab === 'external' && (
              <label className="field">
                <span>模型 ID</span>
                <input
                  className="input"
                  value={form.model}
                  onChange={(e) => patch('model', e.target.value)}
                  placeholder="deepseek-flash"
                />
              </label>
            )}

            <label className="field">
              <span>API Key{tab === 'local' && '（LM Studio 通常任意填即可）'}</span>
              <input
                className="input"
                type="password"
                value={form.api_key}
                onChange={(e) => patch('api_key', e.target.value)}
                placeholder={tab === 'local' ? 'lm-studio' : 'sk-…'}
              />
            </label>
          </div>
        )}

        <div className="settings-actions">
          <button className="btn ghost" onClick={() => void doTest()} disabled={busy}>
            测试连接
          </button>
          <button className="btn primary" onClick={() => void save()} disabled={busy}>
            保存配置
          </button>
          {tab !== 'mock' && active !== tab && (
            <button className="btn primary" onClick={() => void save(true)} disabled={busy}>
              保存并启用
            </button>
          )}
        </div>

        {test && (
          <div className={`settings-result ${test.ok ? 'ok' : 'bad'}`}>
            <strong>{test.ok ? '✓ 连接成功' : '✕ 连接失败'}</strong>
            <span>
              模型 {test.model || '—'} · 握手 {test.handshake || '—'} · 延迟 {test.latency_ms ?? '—'} ms
            </span>
            {test.detail && <span>{test.detail}</span>}
          </div>
        )}
        {err && <div className="settings-result bad">{err}</div>}
      </div>

      <div className="settings-stats">
        <div><small>生效提供方</small><b>{META[active].title}</b></div>
        <div><small>模型</small><b>{data.providers[active].model || '—'}</b></div>
        <div><small>限流间隔</small><b>{rl.current_interval ?? '—'} s</b></div>
        <div><small>429 累计</small><b>{rl.total_rate_limited ?? 0}</b></div>
        <div><small>调用 / 成功</small><b>{rl.total_calls ?? 0} / {rl.total_success ?? 0}</b></div>
        <button className="btn ghost sm" onClick={() => void doPing()}>自检当前链路</button>
      </div>
      {ping && <pre className="settings-pre">{JSON.stringify(ping, null, 2)}</pre>}

      {/* ---------------- RAG / SiliconFlow Reranker 实验台 ---------------- */}
      <div className="settings-panel rag-panel">
        <div className="settings-panel-head">
          <div>
            <div className="settings-kicker">RETRIEVAL / RERANK</div>
            <h2>RAG 商品知识库 · 硅基流动 Reranker</h2>
          </div>
          <span className={`rag-state ${rag?.available ? 'on' : 'off'}`}>
            <i /> {rag?.available ? '已接入 · 可用' : '未接入 / 降级中'}
          </span>
        </div>
        <p className="rag-lead">
          <b>双阶段 · 双模型（均走硅基流动）</b>：一阶段
          <code> {rag?.embedding?.embed_model || 'BAAI/bge-m3'} </code>
          向量召回 + 本地关键词召回，按<b>置信度加权 RRF</b> 融合（关键词路自己没把握时权重趋近 0，
          避免噪声候选挤掉语义结果）；二阶段
          <code> {rag?.rerank_model || 'BAAI/bge-reranker-v2-m3'} </code>
          语义精排 → Top-{rag?.top_k ?? 3} 注入售前 Agent。
          <b>任一模型超时 / 429 / 无 key 都自动降级为关键词召回，客服不会断。</b>
          {ragResult?.retrieval && (
            <span className="rag-fusion-note">
              本次融合：关键词置信度 {ragResult.retrieval.kw_confidence ?? '—'} ·
              权重 kw {ragResult.retrieval.w_keyword ?? '—'} / vec {ragResult.retrieval.w_vector ?? '—'}
              （{ragResult.retrieval.fusion === 'equal-rrf' ? '等权对照' : '自适应加权'}）
            </span>
          )}
        </p>

        <div className="rag-chips">
          <div className="rag-chip"><small>向量模型 / 维度</small><b>{rag?.embedding?.embed_model?.split('/').pop() || '—'} · {rag?.embedding?.dim ?? '—'}维</b></div>
          <div className="rag-chip"><small>文档向量缓存</small><b>{rag?.embedding?.cached_docs ?? '—'} 条</b></div>
          <div className="rag-chip"><small>商品文档</small><b>{rag?.catalog_size ?? '—'} 个 SKU</b></div>
          <div className="rag-chip"><small>嵌入调用 / 缓存命中</small><b>{rag?.embedding?.total_calls ?? 0} / {rag?.embedding?.total_cached ?? 0}</b></div>
          <div className="rag-chip"><small>精排调用 / 降级</small><b>{rag?.total_calls ?? 0} / {rag?.total_degraded ?? 0}</b></div>
          <button className="btn ghost sm" onClick={refreshRag}>刷新状态</button>
        </div>

        <div className="rag-block">
          <h3>① 裸重排测试 · 随便点</h3>
          <p className="hint">同一组候选文档，看 reranker 给的相关性分数（与商品库无关，纯测接口）。</p>
          <div className="rag-row">
            <input
              className="input"
              value={rerankQuery}
              onChange={(e) => setRerankQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void doRerank()}
              placeholder="输入一个问题"
            />
            <button className="btn primary" onClick={() => void doRerank()} disabled={ragBusy}>
              重排一下 →
            </button>
          </div>
          {rerankResult && (
            <div className="rag-scores">
              {rerankResult.results.map((h) => {
                const pct = Math.max(2, Math.min(100, (h.score ?? 0) * 100))
                return (
                  <div className="rag-score-row" key={h.index}>
                    <span className="rag-score-text">{h.text}</span>
                    <span className="rag-score-bar"><i style={{ width: `${pct}%` }} /></span>
                    <b>{h.score === null ? '降级' : h.score.toFixed(4)}</b>
                  </div>
                )
              })}
              <div className="rag-foot">
                <span className={rerankResult.degraded ? 'rag-bad' : 'rag-good'}>
                  {rerankResult.degraded ? `[${rerankResult.code}] 已降级：${rerankResult.detail}` : '✓ 真实调用 SiliconFlow /v1/rerank'}
                </span>
                <span>模型 {rerankResult.model}</span>
                <span>{rerankResult.cached ? '缓存命中' : '实时请求'}</span>
                <span>{rerankResult.latency_ms ?? '—'} ms</span>
              </div>
            </div>
          )}
        </div>

        <div className="rag-block">
          <h3>② 商品知识库试搜 · 看召回前后顺序变化</h3>
          <div className="rag-row">
            <input
              className="input"
              value={ragQuery}
              onChange={(e) => setRagQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void doRagSearch()}
              placeholder="例如：适合小手的静音无线鼠标"
            />
            <button className="btn primary" onClick={() => void doRagSearch()} disabled={ragBusy}>
              检索并精排 →
            </button>
          </div>
          <div className="rag-modes">
            <span className="rag-modes-label">召回策略（现场对比）：</span>
            {([
              ['hybrid', '加权 RRF（默认）'],
              ['hybrid_equal', 'v1 等权 RRF'],
              ['vector', '纯向量'],
              ['keyword', '纯关键词'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                className={`rag-mode ${recallMode === value ? 'active' : ''}`}
                onClick={() => { setRecallMode(value); void doRagSearch(ragQuery, value) }}
                disabled={ragBusy}
                title={value === 'hybrid_equal' ? '评测留作对照的旧版等权融合' : undefined}
              >{label}</button>
            ))}
          </div>
          <div className="rag-presets">
            {PRESETS.map((p) => (
              <button key={p} className="rag-preset" onClick={() => void doRagSearch(p)} disabled={ragBusy}>
                {p}
              </button>
            ))}
            <button
              className="rag-preset ghost"
              onClick={() => void doRagSearch(ragQuery)}
              disabled={ragBusy}
              title="随便点，没有副作用"
            >↻ 再来一次</button>
          </div>

          {ragResult && (
            <div className="rag-grid">
              <div className="rag-col">
                <h4>一阶段 · 关键词召回 <span>{ragResult.recall_k ?? 0} 条候选</span></h4>
                {(ragResult.candidates || []).slice(0, 6).map((c) => (
                  <div className="rag-item" key={c.sku}>
                    <em>#{c.recall_rank}</em>
                    <div><b>{c.name}</b><small>{c.sku} · ¥{c.price} · 召回分 {c.recall_score}</small></div>
                  </div>
                ))}
              </div>
              <div className="rag-col">
                <h4>二阶段 · Reranker 精排 <span>Top {ragResult.top_k ?? 0}</span></h4>
                {(ragResult.results || []).map((h) => (
                  <div className="rag-item ranked" key={h.sku}>
                    <em>#{h.rank}</em>
                    <div>
                      <b>{h.name}</b>
                      <small>
                        {h.sku} · ¥{h.price} · 相关度 {h.score === null ? '—' : h.score.toFixed(4)}
                        {' · 由 #'}{h.recall_rank}{' '}
                        {h.moved > 0 ? `↑ 提前 ${h.moved}` : h.moved < 0 ? `↓ 后退 ${-h.moved}` : '— 不变'}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {ragResult && (ragResult.code || ragResult.detail) && (
            <div className={`settings-result ${ragResult.degraded ? 'bad' : 'ok'}`}>
              <strong>{ragResult.degraded ? `[${ragResult.code}] 已降级为关键词召回顺序` : '✓ 检索完成'}</strong>
              {ragResult.detail && <span>{ragResult.detail}</span>}
              {ragResult.rerank && (
                <span>
                  {ragResult.rerank.model} · {ragResult.rerank.cached ? '缓存命中' : '实时请求'} · {ragResult.rerank.latency_ms ?? '—'} ms
                </span>
              )}
            </div>
          )}
          {ragMsg && <div className="settings-result bad">{ragMsg}</div>}
        </div>
      </div>

      {/* ---------------- 说明书知识库：自动入库与章节问答 ---------------- */}
      <div className="settings-panel rag-panel kb-panel">
        <div className="settings-panel-head">
          <div>
            <div className="settings-kicker">KNOWLEDGE / MANUALS</div>
            <h2>说明书知识库 · 自动入库与章节问答</h2>
          </div>
          <span className={`rag-state ${kb?.total_docs ? 'on' : 'off'}`}>
            <i /> {kb?.total_docs ? `已入库 ${kb.total_docs} 个章节` : '尚未同步'}
          </span>
        </div>
        <p className="rag-lead">
          资源下载中心的说明书 / 快速入门 / 保修卡自动切片入库，与下载文件<b>共用同一份正文</b>；
          客服回答「怎么配对 / 怎么设置 / 故障排查」时检索章节原文，<b>带型号、文档名与章节号引用</b>，
          不靠模型记忆编造。商品参数文档与说明书章节是两个独立索引域，互不污染。
        </p>

        <div className="rag-chips">
          <div className="rag-chip"><small>章节切片</small><b>{kb?.total_docs ?? '—'}</b></div>
          <div className="rag-chip"><small>用户手册 / 资源</small><b>{kb?.manual_docs ?? '—'} / {kb?.resource_docs ?? '—'}</b></div>
          <div className="rag-chip"><small>覆盖型号</small><b>{kb?.sku_count ?? '—'} 个 SKU</b></div>
          <div className="rag-chip"><small>商品参数文档</small><b>{kb?.product_docs ?? '—'} 个</b></div>
          <button className="btn ghost sm" onClick={() => void doKbSync()} disabled={kbBusy}>
            重新同步说明书
          </button>
        </div>
        <p className="kb-sync-note">
          自动同步在服务启动时执行，按内容 SHA256 指纹幂等：说明书没改就不重建，改了才整体替换该来源的切片。
          {kb?.last_synced_at && <> 最近同步：{kb.last_synced_at.replace('T', ' ').slice(0, 19)}。</>}
        </p>
        {kbSync && (
          <div className={`settings-result ${kbSync.ok ? 'ok' : 'bad'}`}>
            <strong>{kbSync.ok ? '✓ 同步完成（幂等）' : '✕ 同步失败'}</strong>
            <span>{kbSync.detail}</span>
            <span>
              新建 {kbSync.created ?? 0} · 更新 {kbSync.updated ?? 0} · 未变 {kbSync.unchanged ?? 0} · 清理 {kbSync.removed ?? 0}
            </span>
          </div>
        )}

        <div className="rag-block">
          <h3>说明书试搜 · 看命中了哪一节</h3>
          <p className="hint">直接问使用问题；填型号则只在该型号说明书内检索，等价于客服带上 SKU 槽位调用。</p>
          <div className="rag-row">
            <input
              className="input"
              value={kbQuery}
              onChange={(e) => setKbQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void doKbSearch()}
              placeholder="例如：键盘怎么配对蓝牙"
            />
            <input
              className="input kb-sku"
              value={kbSku}
              onChange={(e) => setKbSku(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void doKbSearch()}
              placeholder="型号(可选)"
            />
            <button className="btn primary" onClick={() => void doKbSearch()} disabled={kbBusy}>
              检索说明书 →
            </button>
          </div>
          <div className="rag-presets">
            {KB_PRESETS.map((p) => (
              <button key={p.q} className="rag-preset" onClick={() => void doKbSearch(p.q, p.sku)} disabled={kbBusy}>
                {p.q}
              </button>
            ))}
          </div>

          {kbResult && (
            <div className="kb-hits">
              {(kbResult.results || []).map((h) => (
                <div className="kb-hit" key={`${h.sku}-${h.doc_title}-${h.section_no}-${h.rank}`}>
                  <div className="kb-hit-head">
                    <em>#{h.rank}</em>
                    <div>
                      <b>{h.sku} · {h.doc_title}</b>
                      <small>
                        第 {h.section_no} 节「{h.section_title}」· 相关度 {h.score === null ? '降级' : h.score.toFixed(4)}
                        {' · 召回 #'}{h.recall_rank}（{h.recall_score}）
                      </small>
                    </div>
                    {h.file_url && (
                      <a className="kb-dl" href={`${apiBase()}${h.file_url}`} target="_blank" rel="noreferrer">
                        下载原文 ↗
                      </a>
                    )}
                  </div>
                  <p>{h.content}</p>
                </div>
              ))}
              {!(kbResult.results || []).length && <div className="hint">没有命中章节，换个问法试试。</div>}
              <div className="rag-foot">
                <span className={kbResult.degraded ? 'rag-bad' : 'rag-good'}>
                  {kbResult.degraded
                    ? `[${kbResult.code}] 已降级为关键词召回：${kbResult.detail}`
                    : '✓ 已调用 Reranker 精排'}
                </span>
                <span>检索池 {kbResult.doc_count} 个切片 · 召回 {kbResult.recall_k} · 返回 {kbResult.top_k}</span>
                <span>{kbResult.rerank?.latency_ms ?? '—'} ms</span>
              </div>
            </div>
          )}
          {kbResult?.observation && (
            <details className="kb-obs">
              <summary>查看客服实际收到的 observation（带引用话术）</summary>
              <pre>{kbResult.observation}</pre>
            </details>
          )}
          {kbMsg && <div className="settings-result bad">{kbMsg}</div>}
        </div>
      </div>

      {/* ---------------- 联网搜索：阿里云百炼 EnhancedSearch MCP ---------------- */}
      <div className="settings-panel rag-panel ws-panel">
        <div className="settings-panel-head">
          <div>
            <div className="settings-kicker">WEB SEARCH / MCP</div>
            <h2>联网搜索 · 阿里云百炼 EnhancedSearch</h2>
          </div>
          <span className={`rag-state ${ws?.available ? 'on' : 'off'}`}>
            <i /> {ws?.available ? '已接入 · 可用' : '未接入 / 降级中'}
          </span>
        </div>
        <p className="rag-lead">
          本地商品库 / 说明书覆盖不到的外部通用概念、行业名词、新品行情与时效资讯，
          经 MCP 调 <code>{ws?.tool || 'search_pro'}</code> 检索后注入售前 Agent；
          <b>本店商品参数 / 价格 / 库存以页面为准，联网结果仅作参考。</b>
          key 为空、超时、鉴权失败、解析失败一律降级，客服对话不会中断。
        </p>

        <div className="rag-chips">
          <div className="rag-chip"><small>MCP 工具</small><b>{ws?.tool || '—'}</b></div>
          <div className="rag-chip"><small>调用 / 成功</small><b>{ws?.total_calls ?? 0} / {ws?.total_success ?? 0}</b></div>
          <div className="rag-chip"><small>降级 / 缓存命中</small><b>{ws?.total_degraded ?? 0} / {ws?.total_cached ?? 0}</b></div>
          <div className="rag-chip"><small>平均延迟</small><b>{ws?.avg_latency_ms ?? '—'} ms</b></div>
          <button className="btn ghost sm" onClick={refreshWs}>刷新状态</button>
        </div>

        <div className="rag-block">
          <h3>联网试搜 · 看命中来源与客服收到的话术</h3>
          <div className="rag-row">
            <input
              className="input"
              value={wsQuery}
              onChange={(e) => setWsQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void doWsSearch()}
              placeholder="例如：磁轴键盘是什么原理"
            />
            <button className="btn primary" onClick={() => void doWsSearch()} disabled={wsBusy}>
              联网检索 →
            </button>
          </div>
          <div className="rag-presets">
            {WS_PRESETS.map((p) => (
              <button key={p} className="rag-preset" onClick={() => void doWsSearch(p)} disabled={wsBusy}>
                {p}
              </button>
            ))}
          </div>

          {wsResult && (
            <div className="kb-hits">
              {(wsResult.results || []).map((h, i) => (
                <div className="kb-hit" key={`${h.url}-${i}`}>
                  <div className="kb-hit-head">
                    <em>#{i + 1}</em>
                    <div>
                      <b>{h.title}</b>
                      <small>
                        {h.hostname || h.url} · {wsResult.cached ? '缓存命中' : '实时请求'} · {wsResult.latency_ms ?? '—'} ms
                      </small>
                    </div>
                    {h.url && (
                      <a className="kb-dl" href={h.url} target="_blank" rel="noreferrer">
                        打开来源 ↗
                      </a>
                    )}
                  </div>
                  <p>{h.snippet}</p>
                </div>
              ))}
              {!(wsResult.results || []).length && <div className="hint">没有检索到相关公开内容，换个问法试试。</div>}
              <div className="rag-foot">
                <span className={wsResult.degraded ? 'rag-bad' : 'rag-good'}>
                  {wsResult.degraded ? `[${wsResult.code}] 已降级：${wsResult.detail}` : '✓ 已调用百炼 EnhancedSearch'}
                </span>
                <span>返回 {wsResult.results.length} 条</span>
              </div>
            </div>
          )}
          {wsResult?.observation && (
            <details className="kb-obs">
              <summary>查看客服实际收到的 observation（带引用话术）</summary>
              <pre>{wsResult.observation}</pre>
            </details>
          )}
          {wsMsg && <div className="settings-result bad">{wsMsg}</div>}
        </div>
      </div>

      <div className="settings-about">
        <h3>关于你下载的两个 BGE 模型</h3>
        <p>
          <b>Bge M3 GGUF</b> 是嵌入（embedding）模型，<b>Bge Reranker v2 M3</b> 是重排序（reranker）模型。
          它们负责「检索」，不会生成对话，所以<b>不能选作客服 LLM</b> —— 上面本地模型下拉里已标灰禁用。
        </p>
        <p>
          它们的正确用途是 <b>RAG 商品知识库链路</b>：26 个 SKU 的规格文档 → 召回候选 → Reranker 排序 →
          Top-K 参数塞进 Agent 的 observations，这样回答规格/兼容/推荐问题有据可依，不靠模型记忆。
          <b>现在这条链路已接入</b>：上面实验台和售前 Agent 的 <code>search_products</code> 工具都会调用它；
          精排用的是硅基流动云端 <code>BAAI/bge-reranker-v2-m3</code>（免费额度够演示），
          本地两个 BGE 模型可以留作以后做向量召回 / 离线精排的替换件。
        </p>
        <p className="hint">
          降级说明：检索增强 ≠ 检索依赖。key 为空、超时、429、返回异常都会带
          <code> RAG001~003 </code> 错误码并回退到关键词召回顺序；Mock 对话模式默认不发外部请求，保持离线确定性。
        </p>
        <p className="hint">
          另一个坑：<code>qwen3</code> 之类思考型模型会把内容放进 reasoning_content、正文为空；
          LM Studio 也不支持 <code>response_format: json_object</code>。系统已做协议兼容（自动降级 + 容错解析），
          但演示建议优先选 <code>*-instruct</code> / <code>*-coder</code> 指令模型，响应更快更稳。
        </p>
        <p className="hint">
          <b>Qwen3 VL 8B 实测结论</b>：纯文本客服链路全通（意图分类 / 工具选择 / RAG 推荐 /
          二次确认卡 / 转人工 / 闲聊），工具名不臆造、无 thinking 标签泄漏，端到端约 6~21s。
          但当前 LM Studio 加载状态下<b>图像输入返回 400 terminated</b>，视觉通道没跑起来；
          项目目前是纯文本链路，不影响演示。要用图片识别，需先在 LM Studio 正确加载视觉适配器，
          再给 chat 消息增加 <code>image_url</code> 多模态通道。
        </p>
      </div>

      {toast && <div className="settings-toast">{toast}</div>}
    </div>
  )
}
