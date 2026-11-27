import { useEffect, useState } from 'react'
import { createOrder, getMyOrders, staticUrl } from '../api'
import { useCart } from '../store'
import type { OrderPublic } from '../types'

const STATUS_LABEL: Record<string, string> = {
  pending_payment: '待支付', paid: '已支付/待发货', shipped: '已发货',
  delivered: '已签收', done: '已完成', cancelled: '已取消',
  refunding: '退款中', refunded: '已退款',
}

export default function Checkout() {
  const { lines, total, clear } = useCart()
  const [addr, setAddr] = useState({
    province: '广东省', city: '深圳市', district: '南山区',
    detail: '科技园路 88 号 A 座 1203', contact: '演示用户', phone: '13800001234',
  })
  const [placed, setPlaced] = useState<OrderPublic | null>(null)
  const [err, setErr] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [orders, setOrders] = useState<OrderPublic[]>([])

  useEffect(() => {
    getMyOrders('buyer_demo').then(setOrders).catch(() => setOrders([]))
  }, [])

  const submit = async () => {
    if (lines.length === 0) {
      setErr('购物车为空')
      return
    }
    setSubmitting(true)
    setErr('')
    try {
      const res = await createOrder({
        buyer_id: 'buyer_demo',
        lines: lines.map((l) => ({ product_id: l.product.id, qty: l.qty })),
        address: addr,
      })
      setPlaced(res)
      clear()
      setOrders(await getMyOrders('buyer_demo'))
    } catch (e: any) {
      setErr(e.message || '下单失败')
    } finally {
      setSubmitting(false)
    }
  }

  if (placed) {
    return (
      <div className="checkout-success">
        <h2>下单成功 🎉</h2>
        <div className="card">
          <p>订单尾号：<b>{placed.order_no_tail}</b></p>
          <p>状态：<b>{STATUS_LABEL[placed.status] || placed.status}</b></p>
          <p>金额：<b>¥{placed.total}</b></p>
          <p>收货：{placed.address_masked}</p>
          <p className="muted">演示：下单即视为已支付，您可在右下角客服里「改地址 / 取消」该订单体验写操作二次确认。</p>
        </div>
        <button className="btn ghost" onClick={() => setPlaced(null)}>
          继续购物
        </button>
      </div>
    )
  }

  return (
    <div className="checkout">
      <h2 className="section-title">结算</h2>
      <div className="checkout-grid">
        <div className="card">
          <h3>收货信息</h3>
          <div className="form-grid">
            <input className="input" placeholder="省" value={addr.province} onChange={(e) => setAddr({ ...addr, province: e.target.value })} />
            <input className="input" placeholder="市" value={addr.city} onChange={(e) => setAddr({ ...addr, city: e.target.value })} />
            <input className="input" placeholder="区/县" value={addr.district} onChange={(e) => setAddr({ ...addr, district: e.target.value })} />
            <input className="input" placeholder="详细地址" value={addr.detail} onChange={(e) => setAddr({ ...addr, detail: e.target.value })} />
            <input className="input" placeholder="联系人" value={addr.contact} onChange={(e) => setAddr({ ...addr, contact: e.target.value })} />
            <input className="input" placeholder="手机号" value={addr.phone} onChange={(e) => setAddr({ ...addr, phone: e.target.value })} />
          </div>
        </div>

        <div className="card">
          <h3>订单明细</h3>
          {lines.length === 0 && <p className="muted">购物车为空，先去逛逛吧。</p>}
          {lines.map((l) => (
            <div key={l.product.id} className="order-line">
              <img
                className="cart-thumb"
                src={staticUrl(l.product.image)}
                alt={l.product.name}
                onError={(e) => { e.currentTarget.style.display = 'none' }}
              />
              <span>{l.product.name} × {l.qty}</span>
              <span>¥{l.product.price * l.qty}</span>
            </div>
          ))}
          <div className="order-total">
            <span>合计</span>
            <b>¥{total}</b>
          </div>
          {err && <p className="error">{err}</p>}
          <button className="btn primary block" onClick={submit} disabled={submitting || lines.length === 0}>
            {submitting ? '提交中…' : '提交订单'}
          </button>
        </div>
      </div>

      {orders.length > 0 && (
        <div className="card my-orders">
          <h3>我的订单（演示买家 buyer_demo）</h3>
          {orders.map((o) => (
            <div key={o.order_no_tail + o.status} className="order-line">
              <span>尾号 {o.order_no_tail} · {STATUS_LABEL[o.status] || o.status}</span>
              <span>¥{o.total}</span>
            </div>
          ))}
          <p className="muted">这些是预置演示订单，可在客服里查单、改地址或退换。</p>
        </div>
      )}
    </div>
  )
}
