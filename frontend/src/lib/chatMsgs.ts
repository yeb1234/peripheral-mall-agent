/**
 * 客服消息状态的「纯函数」更新层（无 React 依赖，可单独测试）。
 *
 * 为什么必须纯：React.StrictMode 在开发模式下会故意把 setState 的 updater
 * 调用两次，用来检测副作用。旧实现是
 *     const c = [...m]; c[c.length - 1].content += ch
 * 数组虽然拷贝了，但元素对象仍是同一引用，`+=` 直接改到了 state 上，
 * 于是 StrictMode 双调用把每个 token 追加了两遍 → 页面出现
 * 「KKBB--PPRROO8877 是是否否支支持持」这种逐字重复。
 *
 * 改成不可变更新后，updater 无论被调用几次，返回值都完全一致，
 * dev（StrictMode）与生产构建行为统一。
 */
import type { ChatMeta } from '../types'

export interface Msg {
  role: 'user' | 'assistant'
  content: string
  confirmation?: ChatMeta['confirmation'] | null
  needBind?: boolean
  error?: boolean
  queued?: boolean
}

/** 只替换最后一条 assistant 消息；条件不满足时原样返回（保持引用稳定，避免无谓重渲染）。 */
export function patchLastMsg(msgs: Msg[], patch: Partial<Msg>): Msg[] {
  if (msgs.length === 0) return msgs
  const last = msgs[msgs.length - 1]
  if (last.role !== 'assistant') return msgs
  return [...msgs.slice(0, -1), { ...last, ...patch }]
}

/**
 * 追加一个流式 token。
 * 若最后一条是「排队中…」占位（queued=true），首个 token 到达时先清掉占位，
 * 避免提示文字粘在回复正文前面。
 */
export function appendToken(msgs: Msg[], ch: string): Msg[] {
  const last = msgs[msgs.length - 1]
  if (!last || last.role !== 'assistant') return msgs
  const base = last.queued ? '' : last.content
  return patchLastMsg(msgs, { content: base + ch, queued: false })
}

/** 设置排队提示（仅在最后一条 assistant 气泡还没有内容时生效）。 */
export function setQueueHint(msgs: Msg[], position: number): Msg[] {
  const last = msgs[msgs.length - 1]
  if (!last || last.role !== 'assistant' || last.content) return msgs
  const hint = position > 0
    ? `模型排队中…（前面还有 ${position} 个请求，限流控制系统已自动排队）`
    : '模型排队中…（限流控制系统已自动排队）'
  return patchLastMsg(msgs, { content: hint, queued: true })
}

/** 写入 meta 事件带来的二次确认卡 / 身份核验标记。 */
export function applyMeta(msgs: Msg[], meta: ChatMeta): Msg[] {
  return patchLastMsg(msgs, {
    confirmation: meta.confirmation || null,
    needBind: meta.need_bind,
  })
}

/** 错误：覆盖最后一条 assistant 气泡；若末尾不是 assistant 则追加一条新气泡。 */
export function setError(msgs: Msg[], text: string): Msg[] {
  const last = msgs[msgs.length - 1]
  if (!last || last.role !== 'assistant') {
    return [...msgs, { role: 'assistant', content: text, error: true }]
  }
  return patchLastMsg(msgs, { content: text, error: true, queued: false })
}

/** 流结束兜底：空气泡 / 残留排队占位 → 带错误码的明确提示，绝不留下空白。 */
export function ensureNotBlank(
  msgs: Msg[],
  text = '[SYS000] 抱歉，本次未能生成回复，请重试或切换 mock 模式。',
): Msg[] {
  const last = msgs[msgs.length - 1]
  if (!last || last.role !== 'assistant' || last.error) return msgs
  if (last.content && !last.queued) return msgs
  return patchLastMsg(msgs, { content: text, error: true, queued: false })
}
