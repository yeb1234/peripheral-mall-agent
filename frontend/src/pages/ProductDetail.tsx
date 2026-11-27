import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getProduct, staticUrl, apiBase, memoryEvent } from '../api'
import { useCart } from '../store'
import type { Product } from '../types'

function renderSpec(v: any): string {
  if (Array.isArray(v)) return v.join('、')
  if (typeof v === 'boolean') return v ? '支持' : '不支持'
  return String(v)
}

/** 规格可能是分组结构 {组名: {属性: 值}} 或平铺结构 {属性: 值}，统一成「分组列表」。 */
function groupSpecs(specs: Record<string, any>): { group: string; rows: [string, any][] }[] {
  const groups: { group: string; rows: [string, any][] }[] = []
  for (const [k, v] of Object.entries(specs)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      groups.push({ group: k, rows: Object.entries(v) as [string, any][] })
    } else {
      groups.push({ group: '基础参数', rows: [[k, v]] })
    }
  }
  return groups
}

export default function ProductDetail() {
  const { ident } = useParams()
  const [p, setP] = useState<Product | null>(null)
  const [qty, setQty] = useState(1)
  const [err, setErr] = useState('')
  const [added, setAdded] = useState(false)
  const [imgErr, setImgErr] = useState(false)
  const trackedRef = useRef<string>('')
  const { add } = useCart()

  useEffect(() => {
    setErr('')
    setP(null)
    setAdded(false)
    setImgErr(false)
    getProduct(ident!)
      .then((prod) => {
        setP(prod)
        // 浏览行为进用户行为流；ref 去重避免 StrictMode 双调用记两条
        if (trackedRef.current !== prod.sku) {
          trackedRef.current = prod.sku
          void memoryEvent('view_product', prod.sku, prod.name)
        }
      })
      .catch(() => setErr('商品不存在'))
  }, [ident])

  if (err) return <p className="empty">{err}</p>
  if (!p) return <p className="empty">加载中…</p>

  const askAgent = () =>
    window.dispatchEvent(new CustomEvent('p4-ask', { detail: `${p.sku} 的规格和兼容系统能介绍下吗？` }))

  const groups = groupSpecs(p.specs)

  return (
    <div className="detail">
      <div className="detail-left">
        <div className="detail-media">
          {imgErr ? (
            <span className="product-thumb-text big">{p.name.slice(0, 2)}</span>
          ) : (
            <img
              src={staticUrl(p.image)}
              alt={p.name}
              onError={() => setImgErr(true)}
            />
          )}
        </div>
      </div>
      <div className="detail-right">
        <span className="tag">{p.brand}</span>
        <h1>{p.name}</h1>
        <div className="price big">¥{p.price}</div>
        <p className="muted">库存 {p.stock} 件 · SKU {p.sku}</p>
        <p>{p.description}</p>

        <h3>规格参数</h3>
        <div className="spec-groups">
          {groups.map((g) => (
            <div className="spec-group" key={g.group}>
              <div className="spec-group-title">{g.group}</div>
              <table className="spec-table">
                <tbody>
                  {g.rows.map(([k, v]) => (
                    <tr key={k}>
                      <td className="spec-k">{k}</td>
                      <td>{renderSpec(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>

        <div className="detail-actions">
          <div className="qty">
            <button onClick={() => setQty((q) => Math.max(1, q - 1))}>−</button>
            <span>{qty}</span>
            <button onClick={() => setQty((q) => q + 1)}>+</button>
          </div>
          <button
            className="btn primary"
            onClick={() => {
              add(p, qty)
              void memoryEvent('add_to_cart', p.sku, `${p.name} ×${qty}`, { qty })
              setAdded(true)
              setTimeout(() => setAdded(false), 1500)
            }}
          >
            {added ? '已加入购物车' : '加入购物车'}
          </button>
          <button className="btn ghost" onClick={askAgent}>
            问客服这款
          </button>
        </div>

        {p.drivers && p.drivers.length > 0 && (
          <div className="driver-box">
            <h3>官方驱动</h3>
            <ul>
              {p.drivers.map((d) => (
                <li key={d.id}>
                  <span>{d.os} / {d.arch} · v{d.version} · {d.size_mb}MB</span>
                  <a
                    className="btn ghost sm"
                    href={`${apiBase()}${d.file_url}`}
                    download
                    onClick={() => void memoryEvent('driver_download', p.sku, `${d.os} v${d.version}`)}
                  >
                    下载
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
