import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { chatSSE, confirmAction, ensureBuyerToken, memoryResume, memorySnapshot, verifySession } from '../api'
import type { MemorySnapshot } from '../api'
import type { AgentTransfer, ChatMeta, ChatNlu } from '../types'
import {
  appendToken,
  applyMeta,
  ensureNotBlank,
  patchLastMsg,
  setError,
  setQueueHint,
} from '../lib/chatMsgs'
import type { Msg } from '../lib/chatMsgs'
import { useCart } from '../store'

const NLU_INTENT_CN: Record<string, string> = {
  presale: '售前', insale: '售中', aftersale: '售后', chitchat: '闲聊', handoff: '转人工',
}

const AGENT_CN: Record<string, string> = {
  presale: '售前', insale: '售中', aftersale: '售后', handoff: '人工',
}
const NLU_SOURCE_CN: Record<string, string> = {
  llm: 'LLM 改写', rule: '规则短路', fallback: '降级',
}

const SUGGESTIONS = [
  'KB-PRO87 支持 macOS 吗',
  'KB-PRO87 怎么配对蓝牙',
  '磁轴键盘是什么原理',
  '下载 KB-PRO87 的 Windows 驱动',
  '下载 KB-PRO87 的说明书',
  '改一下我的收货地址',
  '我要退掉 HS-ANC7',
  '取消我的待支付订单',
  '转人工',
]

export default function ChatWidget() {
  const { buyerId } = useCart()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(() =>
    typeof localStorage !== 'undefined' ? localStorage.getItem('p4_session') : null,
  )
  const [pendingConfirm, setPendingConfirm] = useState<{ confirm_id: string; preview: string } | null>(null)
  const [needBind, setNeedBind] = useState(false)
  const [phone, setPhone] = useState('')
  const [snap, setSnap] = useState<MemorySnapshot | null>(null)
  const [lastNlu, setLastNlu] = useState<ChatNlu | null>(null)
  const [lastTrail, setLastTrail] = useState<AgentTransfer[]>([])
  const [resuming, setResuming] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const lastSentRef = useRef<string>('')
  // 同步发送锁：React 的 loading state 是异步的，同一 tick 连点两次会双双通过
  const sendingRef = useRef(false)
  const lastTextRef = useRef<{ text: string; ts: number }>({ text: '', ts: 0 })

  useEffect(() => {
    // 回到客服首页时必须滚回顶部，否则欢迎语会被留在视口上方看不到
    if (msgs.length === 0) {
      bodyRef.current?.scrollTo({ top: 0 })
      return
    }
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, loading])

  // Esc 收起客服；通过顶部导航/页脚跳转页面时自动收起，避免面板继续盖住目标页
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  // 打开客服 / 换会话 / 换用户时拉取长期记忆：欢迎页与联动提示都基于它
  const refreshMemory = useCallback(() => {
    memorySnapshot(buyerId, sessionId).then(setSnap).catch(() => setSnap(null))
  }, [buyerId, sessionId])

  useEffect(() => {
    if (open) void refreshMemory()
  }, [open, refreshMemory])

  /**
   * 回到「智能客服自己的首页」：清空当前对话、开启新会话，
   * 重新展示欢迎语与演示问题。不离开当前网页，网站首页仍由顶部导航负责。
   */
  const resetToHome = () => {
    if (loading) return // SSE 还在推流时重置会让旧 token 串进新对话
    setMsgs([])
    setInput('')
    setLastNlu(null)
    setPendingConfirm(null)
    setNeedBind(false)
    setPhone('')
    lastSentRef.current = ''
    setSessionId(null)
    localStorage.removeItem('p4_session')
    void refreshMemory()
  }

  // 接收商品详情页「问客服这款」按钮发来的预填问题
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as string
      setOpen(true)
      setInput(detail)
    }
    window.addEventListener('p4-ask', handler as EventListener)
    return () => window.removeEventListener('p4-ask', handler as EventListener)
  }, [])

  /**
   * 所有 setMsgs 的 updater 都调用 lib/chatMsgs.ts 里的纯函数。
   * 绝不修改传入的 state 对象 —— 否则 React.StrictMode 的双调用会把
   * 每个 token 追加两次（逐字重复的乱码）。
   */
  const send = async (override?: string) => {
    const text = (override ?? input).trim()
    if (!text || loading || sendingRef.current) return
    // 2 秒内同一句重复提交（连点/回车抖动）：本地先丢，后端还有语义去重兜底。
    // override 来自快捷问题/重试按钮，不做重复拦截。
    const now = Date.now()
    if (!override && lastTextRef.current.text === text && now - lastTextRef.current.ts < 2000) return
    lastTextRef.current = { text, ts: now }
    sendingRef.current = true
    lastSentRef.current = text
    setInput('')
    setNeedBind(false)
    setMsgs((m) => [...m, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    setLoading(true)
    try {
      const token = await ensureBuyerToken(buyerId)
      const requestId = globalThis.crypto?.randomUUID?.()
        ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
      await chatSSE(
        {
          session_id: sessionId, buyer_id: buyerId, channel: 'web', message: text,
          token, request_id: requestId,
        },
        {
          onToken: (ch) => setMsgs((m) => appendToken(m, ch)),
          onQueue: (position) => setMsgs((m) => setQueueHint(m, position)),
          onMeta: (meta: ChatMeta) => {
            if (meta.session_id) {
              setSessionId(meta.session_id)
              localStorage.setItem('p4_session', meta.session_id)
            }
            setMsgs((m) => applyMeta(m, meta))
            if (meta.nlu) setLastNlu(meta.nlu)
            setLastTrail(meta.agent_trail || [])
            if (meta.confirmation) setPendingConfirm(meta.confirmation)
            if (meta.need_bind) setNeedBind(true)
          },
          onError: (err) => {
            const msg = err.code ? `[${err.code}] ${err.message}` : err.message
            setMsgs((m) => setError(m, msg))
          },
        },
      )
    } finally {
      // 无论成功失败都解锁：sendingRef 是同步锁，setLoading 是异步状态，两者不能互相替代
      sendingRef.current = false
      setLoading(false)
      setMsgs((m) => ensureNotBlank(m))
      void refreshMemory() // 本轮的偏好/事件/任务已经落库
    }
  }

  const retry = () => {
    if (loading) return
    const t = lastSentRef.current
    if (!t) return
    // 直接把文本传给 send，不能靠 setInput + send()：setState 是异步的，
    // send() 会读到旧 input（空串）而直接 return，导致「重试」按钮点了没反应。
    setInput('')
    void send(t)
  }

  const doConfirm = async (approve: boolean) => {
    if (!pendingConfirm || !sessionId) return
    const cid = pendingConfirm.confirm_id
    setPendingConfirm(null)
    setMsgs((m) => [...m, { role: 'assistant', content: '处理中…' }])
    const res = await confirmAction(sessionId, cid, approve)
    const text = res.observation || (approve ? '已确认。' : '已取消。')
    setMsgs((m) => patchLastMsg(m, { content: text }))
    void refreshMemory() // 任务收口后刷新续点列表
  }

  /** 续点：把挂起任务恢复为进行中，并自动发出带原诉求关键词的话术 */
  const doResume = async (taskId: string) => {
    if (loading || resuming) return
    setResuming(true)
    try {
      const r = await memoryResume(taskId)
      if (r.ok && r.suggested_message) {
        setMsgs((m) => [
          ...m,
          { role: 'assistant', content: `已接上上次的任务「${r.task?.title || ''}」，从断点继续处理…` },
        ])
        void refreshMemory()
        void send(r.suggested_message)
      }
    } catch {
      /* 续点失败不影响客服使用 */
    } finally {
      setResuming(false)
    }
  }

  const doVerify = async () => {
    if (!sessionId || phone.length !== 4) return
    const res = await verifySession(sessionId, phone)
    setNeedBind(false)
    setPhone('')
    setMsgs((m) => [
      ...m,
      {
        role: 'assistant',
        content: res.ok ? `身份核验通过，已绑定 ${res.bound} 笔订单，现在可以查单/改单了。` : `核验失败：${res.reason}`,
      },
    ])
  }

  const ask = (t: string) => {
    setOpen(true)
    setInput(t)
    // 点示例即发，避免演示时点完不知道还要再按一次发送
    void send(t)
  }

  // 记忆中心点「从这里继续」时，直接按续点话术发消息，而不是只预填输入框
  const sendRef = useRef<(t: string) => void>(() => {})
  sendRef.current = (t: string) => void send(t)
  useEffect(() => {
    const handler = (e: Event) => {
      setOpen(true)
      sendRef.current((e as CustomEvent).detail as string)
    }
    window.addEventListener('p4-resume', handler as EventListener)
    return () => window.removeEventListener('p4-resume', handler as EventListener)
  }, [])

  if (!open) {
    return (
      <button className="chat-fab" onClick={() => setOpen(true)}>
        智能客服
      </button>
    )
  }

  const prefText = snap
    ? Object.entries(snap.profile.prefs).map(([k, v]) => `${k} ${v}`).join(' · ') || '暂无记录，聊两句就会记住'
    : ''

  return (
    <div className="chat-panel">
      <div className="chat-head">
        <span className="chat-title">P4 智能客服 · 多 Agent</span>
        <div className="chat-head-actions">
          <button
            className="chat-nav-btn"
            onClick={resetToHome}
            disabled={loading}
            title="回到智能客服首页：清空对话、重新显示演示问题（开启新会话）"
          >
            ⌂ 首页
          </button>
          <button
            className="chat-close"
            onClick={() => setOpen(false)}
            title="收起客服（Esc），留在当前页"
            aria-label="收起客服"
          >
            ×
          </button>
        </div>
      </div>

      <div className={`chat-body${msgs.length === 0 ? ' chat-body-home' : ''}`} ref={bodyRef}>
        {msgs.length === 0 && (
          <>
            <div className="chat-home">
              <div className="chat-home-kicker">智能客服 · 首页</div>
              <p className="chat-home-lead">我是 P4 智能客服，由「总控 + 售前/售中/售后」多 Agent 协作。</p>
              <p>演示买家已自动绑定订单。试试下面的问题：</p>
              <div className="chat-home-caps">
                <span>售前 · 智能推荐 / 查规格</span>
                <span>售中 · 查订单 / 查物流</span>
                <span>售后 · 退货 / 改单（需二次确认）</span>
              </div>
              <p className="chat-home-hint">点右上角「⌂ 首页」可回到这里，并开启新会话。</p>
            </div>

            {snap && (
              <div className="chat-memory">
                <div className="chat-memory-head">
                  <span>已记住你 · {snap.buyer_id}</span>
                  <button onClick={() => void refreshMemory()}>刷新</button>
                </div>
                <div className="chat-memory-row"><b>习惯</b>{prefText}</div>
                <div className="chat-memory-row">
                  <b>购买</b>{snap.purchase.orders} 单 · 累计 ¥{snap.purchase.total_spent.toFixed(0)}
                </div>
                {(snap.profile.stats?.recent_skus || []).length > 0 && (
                  <div className="chat-memory-row">
                    <b>常看</b>{snap.profile.stats.recent_skus.slice(0, 3).join('、')}
                  </div>
                )}
                {snap.tasks.length > 0 && (
                  <div className="chat-memory-row">
                    <b>任务</b>{snap.tasks.slice(0, 2).map((t) => t.title).join(' / ')}
                  </div>
                )}
                {snap.suggested_resume && (
                  <button
                    className="chat-resume-btn"
                    disabled={loading || resuming}
                    onClick={() => void doResume(snap.suggested_resume!.id)}
                  >
                    ▶ 继续上次：{snap.suggested_resume.title}
                  </button>
                )}
              </div>
            )}
          </>
        )}
        {snap && msgs.length > 0 && (
          <div className="chat-context-bar">
            已联动用户记忆：{prefText} · {snap.purchase.orders} 单
            {snap.tasks.length > 0 ? ` · ${snap.tasks.length} 个任务` : ''}
          </div>
        )}
        {lastNlu && msgs.length > 0 && (
          <div className="chat-nlu-bar" title={lastNlu.reason || '第一层语义理解结果'}>
            <span className="chat-nlu-tag">理解</span>
            {lastNlu.rewritten ? <span className="chat-nlu-query">「{lastNlu.rewritten}」</span> : null}
            <span>
              {' → '}{NLU_INTENT_CN[lastNlu.intent || ''] || lastNlu.intent || '—'}
              {' · '}{NLU_SOURCE_CN[lastNlu.source || ''] || lastNlu.source || '—'}
              {(lastNlu.queue_len || 0) > 1 ? ` · ${lastNlu.queue_len} 项诉求` : ''}
              {lastTrail.length > 0
                ? ` · 交接 ${lastTrail.map((t) => `${AGENT_CN[t.from] || t.from}→${AGENT_CN[t.to] || t.to}`).join('，')}`
                : ''}
            </span>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`bubble ${m.role}${m.error ? ' error' : ''}`}>
            {m.content || (loading && i === msgs.length - 1
              ? '模型处理中…（免费 token 限流较严，系统会自动排队重试，可能需要十几秒）'
              : '')}
            {m.error && !loading && (
              <div className="chat-retry">
                <button className="btn ghost sm" onClick={retry}>
                  重试
                </button>
              </div>
            )}
            {m.confirmation && (
              <div className="confirm-card">
                <div className="confirm-title">⚠️ 二次确认</div>
                <div className="confirm-preview">{m.confirmation.preview}</div>
                {m.confirmation.sensitive && (
                  <div className="confirm-warn">高敏操作：金额由策略引擎计算，超限将转人工。</div>
                )}
                <div className="confirm-actions">
                  <button className="btn primary sm" onClick={() => doConfirm(true)}>
                    确认执行
                  </button>
                  <button className="btn ghost sm" onClick={() => doConfirm(false)}>
                    取消
                  </button>
                </div>
              </div>
            )}
            {m.needBind && (
              <div className="verify-card">
                <div>未查询到绑定订单，请输入收货手机后四位核验：</div>
                <div className="verify-row">
                  <input
                    className="input"
                    placeholder="后四位"
                    maxLength={4}
                    value={phone}
                    onChange={(e) => setPhone(e.target.value.replace(/\D/g, ''))}
                  />
                  <button className="btn primary sm" onClick={doVerify}>
                    核验
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {/* 确认卡只在对话框里渲染一次；原来底部还有一条 sticky 确认条，同一张卡会出现两处按钮 */}

      {msgs.length <= 1 && (
        <div className="chat-chips">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" onClick={() => ask(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="chat-input">
        <input
          className="input"
          placeholder="输入问题，回车发送"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
        />
        <button className="btn primary" onClick={() => send()} disabled={loading}>
          发送
        </button>
      </div>
    </div>
  )
}
