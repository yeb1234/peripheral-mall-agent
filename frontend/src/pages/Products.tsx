import { useEffect, useState } from 'react'
import { getProducts, getCategories } from '../api'
import ProductCard from '../components/ProductCard'
import type { Product } from '../types'
import { CAT_META, CAT_ORDER } from '../categories'

const ALL = { key: '', label: '全部' }

export default function Products() {
  const [cat, setCat] = useState('')
  const [q, setQ] = useState('')
  const [list, setList] = useState<Product[]>([])
  const [cats, setCats] = useState<{ key: string; label: string }[]>([ALL])
  const [loading, setLoading] = useState(false)

  // 分类筛选项来自后端 DB，保证与商品库完全一致
  useEffect(() => {
    getCategories()
      .then((rows) => {
        const keys = rows.map((r) => r.key)
        keys.sort((a, b) => CAT_ORDER.indexOf(a) - CAT_ORDER.indexOf(b))
        setCats([ALL, ...keys.map((k) => ({ key: k, label: CAT_META[k]?.name || k }))])
      })
      .catch(() => setCats([ALL]))
  }, [])

  useEffect(() => {
    setLoading(true)
    const t = setTimeout(() => {
      getProducts({ category: cat || undefined, q: q || undefined })
        .then(setList)
        .finally(() => setLoading(false))
    }, 150)
    return () => clearTimeout(t)
  }, [cat, q])

  return (
    <div>
      <h2 className="section-title">全部商品</h2>
      <div className="toolbar">
        <div className="cats">
          {cats.map((c) => (
            <button
              key={c.key}
              className={`chip ${cat === c.key ? 'active' : ''}`}
              onClick={() => setCat(c.key)}
            >
              {c.label}
            </button>
          ))}
        </div>
        <input
          className="input search"
          placeholder="搜索型号 / 品牌 / 参数（如 磁轴、ANC、大手）"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {loading ? (
        <p>加载中…</p>
      ) : (
        <div className="grid">
          {list.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
          {list.length === 0 && <p>没有匹配的商品。</p>}
        </div>
      )}
    </div>
  )
}
