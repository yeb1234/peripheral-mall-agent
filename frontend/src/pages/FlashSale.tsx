import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { flashConfigure, flashReserve, flashStatus, getProducts, staticUrl } from '../api'
import type { FlashStatus } from '../api'
import type { Product } from '../types'

function getBuyerId(): string {
  const id = localStorage.getItem('p4_buyer')
  if (id) return id
  // 演示站默认就是演示买家：干净浏览器打开也能直接查单，不用先核验
  localStorage.setItem('p4_buyer', 'buyer_demo')
  return 'buyer_demo'
}

function fmtCountdown(sec: number): string {
  if (sec <= 0) return '已开售'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  return [h, m, s].map((x) => String(x).padStart(2, '0')).join(':')
}

export default function FlashSale() {
  const [product, setProduct] = useState<Product | null>(null)
  const [sku, setSku] = useState('')
  const [buyerId, setBuyerId] = useState(getBuyerId())
  const [status, setStatus] = useState<FlashStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const timer = useRef<number | null>(null)

  // 选一款无线鼠标作为抢购对象（与后端 lifespan 配置一致：首个无线鼠标）
  useEffect(() => {
    getProducts({}).then((list) => {
      const m = list.find((p) => p.category === 'mouse' && p.name.includes('无线'))
      if (m) {
        setProduct(m)
        setSku(m.sku)
      }
    })
  }, [])

  // 轮询状态
  useEffect(() => {
    if (!sku) return
    const poll = () => flashStatus(sku, buyerId).then(setStatus).catch(() => {})
    poll()
    timer.current = window.setInterval(poll, 2000)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [sku, buyerId])

  const launchTs = status?.launch_at ? status.launch_at * 1000 : 0
  const now = Date.now()
  const remain = Math.max(0, Math.floor((launchTs - now) / 1000))
  const opened = !status?.launch_at || now >= launchTs

  const doReserve = async () => {
    if (!sku) return
    setBusy(true)
    setMsg('')
    try {
      const r = await flashReserve(sku, buyerId)
      if (r.ok) {
        setMsg(`已进入队列，排队第 ${r.position} 位 · 剩余库存 ${r.stock_left}`)
        setStatus(await flashStatus(sku, buyerId))
      } else if (r.reason === '未到开售时间') {
        setMsg('尚未开售，请等待倒计时结束（演示可点“立即开售”）。')
      } else {
        setMsg(r.reason || '抢购失败')
      }
    } finally {
      setBusy(false)
    }
  }

  const openNow = async () => {
    if (!sku) return
    await flashConfigure(sku, 200, null) // 演示：立即开售
    setStatus(await flashStatus(sku, buyerId))
    setMsg('已切换为「立即开售」演示模式，可立即抢购。')
  }

  const switchBuyer = () => {
    const cur = localStorage.getItem('p4_buyer') || 'buyer_demo'
    const next = cur === 'buyer_demo'
      ? 'u_' + Math.random().toString(36).slice(2, 10)
      : 'buyer_demo'
    localStorage.setItem('p4_buyer', next)
    setBuyerId(next)
    setMsg(next === 'buyer_demo'
      ? '已切回演示买家（5 笔订单可查）'
      : `已切换买家身份：${next}（可模拟多人排队；再点一次切回演示买家）`)
  }

  return (
    <div className="flash">
      <div className="flash-hero">
        <div className="flash-info">
          <span className="eyebrow">🔥 限时抢购 · 预售排队</span>
          <h1>{product ? product.name : '加载中…'}</h1>
          <p className="muted">
            开售时间 19:00，库存有限（共 {status?.total ?? 200} 件）。
            高并发下采用「原子库存扣减 + 排队序号」模型，中签后需在有效期内完成下单。
          </p>

          <div className="flash-count">
            <span className="flash-count-label">距开售</span>
            <span className="flash-count-num">{opened ? '已开售' : fmtCountdown(remain)}</span>
          </div>

          <div className="flash-actions">
            <button className="btn primary" onClick={doReserve} disabled={busy || !opened}>
              {opened ? '立即抢购' : '未开售'}
            </button>
            <button className="btn ghost" onClick={openNow}>演示：立即开售</button>
            <button className="btn ghost" onClick={switchBuyer}>切换买家（模拟排队）</button>
          </div>

          {msg && <p className="flash-msg">{msg}</p>}

          {status && (
            <div className="flash-stats">
              <div><b>{status.stock_left ?? '—'}</b><span>剩余库存</span></div>
              <div><b>{status.queue_len ?? '—'}</b><span>排队人数</span></div>
              <div><b>{status.position ?? '—'}</b><span>我的位置</span></div>
              <div><b style={{ color: status.won ? 'var(--ok)' : 'var(--muted)' }}>{status.won ? '已中签' : '未中签'}</b><span>中签状态</span></div>
            </div>
          )}

          {status?.won && product && (
            <Link to={`/products/${product.sku}`} className="btn primary block">
              中签成功 · 去下单 →
            </Link>
          )}
        </div>

        <div className="flash-media">
          {product && (
            <img
              src={staticUrl(product.image)}
              alt={product.name}
              onError={(e) => ((e.currentTarget as HTMLImageElement).style.visibility = 'hidden')}
            />
          )}
        </div>
      </div>

      <p className="muted" style={{ textAlign: 'center', marginTop: 18 }}>
        当前买家身份：<b>{buyerId}</b> · 点「切换买家」可在同一浏览器模拟多人并发抢购，观察排队序号与库存变化。
      </p>
    </div>
  )
}
