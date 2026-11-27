import { useEffect, useState } from 'react'
import { wbSessions, wbMessages, wbAudits } from '../api'
import type { WBSession, WBMessage, WBAudit } from '../api'

export default function Workbench() {
  const [sessions, setSessions] = useState<WBSession[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [msgs, setMsgs] = useState<WBMessage[]>([])
  const [audits, setAudits] = useState<WBAudit[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    wbSessions().then(setSessions).catch(() => setSessions([])).finally(() => setLoading(false))
  }, [])
  useEffect(() => {
    if (!sel) return
    wbMessages(sel).then(setMsgs).catch(() => setMsgs([]))
  }, [sel])
  useEffect(() => {
    wbAudits(sel || undefined).then(setAudits).catch(() => setAudits([]))
  }, [sel])

  return (
    <div>
      <h2 className="section-title">客服工作台（人工接管视图）</h2>
      <p className="muted">
        演示买家 buyer_demo 的会话、消息流与审计留痕。点击左侧会话查看明细；下方审计按当前会话过滤。
      </p>
      <div className="wb">
        <div className="wb-list">
          {loading && <div className="spinner" />}
          {!loading && sessions.length === 0 && <p className="empty">暂无会话。先去右下角客服聊几句。</p>}
          {sessions.map((s) => (
            <div
              key={s.session_id}
              className={`card ${sel === s.session_id ? 'active' : ''}`}
              onClick={() => setSel(s.session_id)}
            >
              <div><b>{s.buyer_id}</b> · {s.channel}</div>
              <div className="wb-session-meta">{s.last_message.slice(0, 30) || '（无消息）'}</div>
              <div className="wb-session-meta">绑定订单 {s.bound_orders} · {s.created_at?.slice(0, 10)}</div>
            </div>
          ))}
        </div>
        <div className="wb-panel">
          <h3>消息流</h3>
          {!sel && <p className="muted">选择一个会话查看消息。</p>}
          {sel && msgs.length === 0 && <p className="muted">该会话暂无消息。</p>}
          {msgs.map((m, i) => (
            <div key={i} className={`wb-msg ${m.role}`}>{m.content}</div>
          ))}
        </div>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3>审计留痕（{sel ? '当前会话' : '全部'}）</h3>
        <table className="audit-table">
          <thead>
            <tr><th>时间</th><th>事件</th><th>执行者</th><th>判定 / 决策</th></tr>
          </thead>
          <tbody>
            {audits.map((a) => (
              <tr key={a.id}>
                <td>{a.created_at?.slice(0, 19)?.replace('T', ' ')}</td>
                <td><span className="tag-ev">{a.event_type}</span></td>
                <td>{a.actor}</td>
                <td>{a.decision}</td>
              </tr>
            ))}
            {audits.length === 0 && (
              <tr><td colSpan={4} className="muted">暂无审计。去客服里试一次「改地址」或「退换」即可生成。</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
