import { useCallback, useEffect, useState } from 'react'
import {
  memoryResume,
  memorySetProfile,
  memorySnapshot,
  memoryTasks,
  type AgentTaskView,
  type MemorySnapshot,
} from '../api'
import { useCart } from '../store'

const KIND_CN: Record<string, string> = {
  view_product: '浏览商品',
  add_to_cart: '加入购物车',
  order_created: '下单成功',
  checkout_submit: '提交结算',
  flash_reserve: '抢购命中',
  flash_attempt: '抢购排队',
  driver_download: '下载驱动',
  resource_download: '下载资源',
  chat: '客服对话',
  confirm_executed: '确认卡执行',
}

function when(iso: string | null): string {
  return iso ? iso.slice(5, 19).replace('T', ' ') : '—'
}

export default function Memory() {
  const { buyerId, setBuyerId } = useCart()
  const [snap, setSnap] = useState<MemorySnapshot | null>(null)
  const [tasks, setTasks] = useState<AgentTaskView[]>([])
  const [draftBuyer, setDraftBuyer] = useState(buyerId)
  const [prefKey, setPrefKey] = useState('')
  const [prefVal, setPrefVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')
  const [err, setErr] = useState('')

  const notify = (s: string) => {
    setToast(s)
    window.setTimeout(() => setToast(''), 2400)
  }

  const load = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([memorySnapshot(buyerId), memoryTasks(buyerId, true)])
      setSnap(s)
      setTasks(t.tasks)
      setErr('')
    } catch (e: any) {
      setErr(e?.message || '记忆服务暂时不可用')
    }
  }, [buyerId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    setDraftBuyer(buyerId)
  }, [buyerId])

  const switchBuyer = () => {
    const v = draftBuyer.trim() || 'buyer_demo'
    setBuyerId(v)
    notify(`已切换到 ${v}，记忆与购物车按用户隔离`)
  }

  const addPref = async () => {
    if (!prefKey.trim() || !prefVal.trim()) return
    setBusy(true)
    try {
      await memorySetProfile(buyerId, undefined, { [prefKey.trim()]: prefVal.trim() })
      setPrefKey('')
      setPrefVal('')
      await load()
      notify('偏好已写入长期记忆')
    } finally {
      setBusy(false)
    }
  }

  const resume = async (t: AgentTaskView) => {
    setBusy(true)
    try {
      const r = await memoryResume(t.id)
      if (r.ok && r.suggested_message) {
        // 让客服窗口直接从断点继续（ChatWidget 监听 p4-resume 自动发送）
        window.dispatchEvent(new CustomEvent('p4-resume', { detail: r.suggested_message }))
        notify('已恢复任务，客服正从断点继续')
        await load()
      } else {
        notify(r.reason || '续点失败')
      }
    } finally {
      setBusy(false)
    }
  }

  if (err && !snap) return <div className="settings-page"><p className="empty">{err}</p></div>

  const stats = snap?.profile.stats || {}
  const prefs = snap?.profile.prefs || {}

  return (
    <div className="settings-page mem-page">
      <div className="settings-head">
        <div>
          <div className="settings-kicker">MEMORY CENTER</div>
          <h1>用户记忆中心</h1>
          <p className="muted">
            长期画像 / 习惯偏好 / 购买记录 / 行为流 / 多任务断点。网站与客服共用同一份用户状态，按 buyer_id 隔离。
          </p>
        </div>
        <button className="btn ghost" onClick={() => void load()}>刷新记忆</button>
      </div>

      <div className="mem-switch card">
        <div>
          <b>当前用户</b>
          <div className="mem-switch-id">{buyerId}</div>
          <small className="muted">切换后会清空本地购物车，后端画像/订单/任务按用户隔离，可用来演示「同一个用户」与「不同用户」。</small>
        </div>
        <div className="mem-switch-form">
          <input
            className="input"
            value={draftBuyer}
            onChange={(e) => setDraftBuyer(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && switchBuyer()}
            placeholder="buyer_demo / buyer_02"
          />
          <button className="btn primary sm" onClick={switchBuyer}>切换用户</button>
        </div>
      </div>

      <div className="mem-stats">
        <div className="card"><small>购买订单</small><b>{snap?.purchase.orders ?? 0}</b><span>累计 ¥{snap?.purchase.total_spent.toFixed(0) ?? 0}</span></div>
        <div className="card"><small>行为事件</small><b>{stats.events ?? 0}</b><span>{Object.keys(stats).filter((k) => !k.startsWith('_') && k !== 'events' && k !== 'last_seen_at').length} 类行为</span></div>
        <div className="card"><small>进行中任务</small><b>{snap?.tasks.length ?? 0}</b><span>可断点续做</span></div>
        <div className="card"><small>客服对话</small><b>{stats.chat ?? 0}</b><span>跨会话保留</span></div>
      </div>

      <div className="mem-grid">
        <section className="card">
          <h3>用户画像与习惯</h3>
          <div className="mem-prefs">
            {Object.entries(prefs).length === 0 && <p className="muted">暂无偏好。在客服里说「适合大手的无线轻量鼠标」会自动抽取手型/连接/重量。</p>}
            {Object.entries(prefs).map(([k, v]) => (
              <span className="mem-pref" key={k}><b>{k}</b>{v}</span>
            ))}
          </div>
          <div className="mem-pref-form">
            <input className="input" placeholder="偏好名（如 手型）" value={prefKey} onChange={(e) => setPrefKey(e.target.value)} />
            <input className="input" placeholder="取值（如 大手）" value={prefVal} onChange={(e) => setPrefVal(e.target.value)} />
            <button className="btn primary sm" disabled={busy} onClick={() => void addPref()}>写入</button>
          </div>
          <div className="mem-kv"><span>最近活跃</span><b>{when(snap?.profile.updated_at || null)}</b></div>
          <div className="mem-kv"><span>常看商品</span><b>{(stats.recent_skus || []).slice(0, 5).join('、') || '—'}</b></div>
        </section>

        <section className="card">
          <h3>购买记录</h3>
          {(snap?.orders || []).length === 0 && <p className="muted">该用户还没有订单。</p>}
          {(snap?.orders || []).map((o: any) => (
            <div className="mem-order" key={o.order_id}>
              <div><b>····{o.order_no_tail}</b><span className={`mem-status s-${o.status}`}>{o.status}</span></div>
              <small>¥{o.total?.toFixed(0)} · {o.items?.map((i: any) => i.sku).join('、')}</small>
            </div>
          ))}
        </section>
      </div>

      <section className="card">
        <h3>多任务与续点 <small className="muted">一个诉求 = 一个任务；确认卡挂起即 checkpoint</small></h3>
        {tasks.length === 0 && <p className="muted">还没有任务。去客服问两个不同问题，就能看到任务被区分开。</p>}
        <div className="mem-tasks">
          {tasks.map((t) => (
            <div className={`mem-task st-${t.status}`} key={t.id}>
              <div className="mem-task-top">
                <b>{t.title}</b>
                <span className={`mem-status s-${t.status}`}>{t.status}</span>
              </div>
              <small>{t.turns} 轮 · 最近「{t.last_message.slice(0, 40)}」· {when(t.updated_at)}</small>
              {Object.keys(t.checkpoint).length > 0 && (
                <pre>{JSON.stringify(t.checkpoint, null, 1).slice(0, 300)}</pre>
              )}
              {t.status !== 'done' && (
                <button className="btn ghost sm" disabled={busy} onClick={() => void resume(t)}>
                  ▶ 从这里继续
                </button>
              )}
            </div>
          ))}
        </div>
      </section>

      <div className="mem-grid">
        <section className="card">
          <h3>行为流</h3>
          {(snap?.events || []).length === 0 && <p className="muted">还没有浏览/加购记录。</p>}
          {(snap?.events || []).map((e) => (
            <div className="mem-event" key={e.id}>
              <span className="mem-event-kind">{KIND_CN[e.kind] || e.kind}</span>
              <b>{e.sku || '—'}</b>
              <small>{e.summary}</small>
              <time>{when(e.created_at)}</time>
            </div>
          ))}
        </section>

        <section className="card">
          <h3>客服看到的记忆</h3>
          <p className="muted">下面这段就是每轮对话注入给 LLM 的上下文摘要（长期记忆 + 行为 + 任务）。</p>
          <pre className="mem-context">{snap?.context || '加载中…'}</pre>
          <h3 className="mt">最近对话</h3>
          {(snap?.dialog || []).slice(-6).map((d, i) => (
            <div className="mem-dialog" key={i}>
              <span className={d.role === 'user' ? 'u' : 'a'}>{d.role === 'user' ? '用户' : '客服'}</span>
              <small>{d.content.slice(0, 90)}</small>
            </div>
          ))}
        </section>
      </div>

      {toast && <div className="settings-toast">{toast}</div>}
    </div>
  )
}
