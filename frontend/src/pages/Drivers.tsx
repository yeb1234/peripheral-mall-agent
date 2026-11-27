import { useEffect, useState } from 'react'
import { apiBase, getProducts, getResources, memoryEvent } from '../api'
import type { Product, ResourceItem } from '../types'

const KIND_TABS = [
  { key: 'all', label: '全部' },
  { key: 'driver', label: '驱动' },
  { key: 'manual', label: '说明书' },
  { key: 'resource', label: '资源' },
]
const CAT_TABS = [
  { key: '', label: '全部品类' },
  { key: 'keyboard', label: '键盘' },
  { key: 'mouse', label: '鼠标' },
  { key: 'headset', label: '耳机' },
]
const KIND_ICON: Record<string, string> = { driver: '⚙️', manual: '📘', resource: '📎' }
const KIND_LABEL: Record<string, string> = { driver: '驱动', manual: '说明书', resource: '资源' }
const RES_LABEL: Record<string, string> = {
  quick_start: '快速入门',
  faq: '常见问题',
  warranty: '保修卡',
  software: '配套软件',
  template: '模板',
}

export default function Resources() {
  const [products, setProducts] = useState<Product[]>([])
  const [items, setItems] = useState<ResourceItem[]>([])
  const [query, setQuery] = useState('')
  const [cat, setCat] = useState('')
  const [kind, setKind] = useState('all')
  const [copied, setCopied] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    getProducts({}).then(setProducts).catch(() => setProducts([]))
  }, [])

  // 拉取资源（带 300ms 防抖，避免每次输入都打接口）
  useEffect(() => {
    setLoading(true)
    const t = setTimeout(() => {
      getResources({ keyword: query.trim(), category: cat, kind })
        .then(setItems)
        .catch(() => setItems([]))
        .finally(() => setLoading(false))
    }, 300)
    return () => clearTimeout(t)
  }, [query, cat, kind])

  const copySha = (id: string, sha: string) => {
    navigator.clipboard?.writeText(sha)
    setCopied(id)
    setTimeout(() => setCopied(''), 1500)
  }

  return (
    <div>
      <h2 className="section-title">资源下载中心 <span className="line" /></h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 18 }}>
        驱动、说明书、保修卡与快速入门指南一站直达。每个文件均带 <b>真实 SHA256</b> 校验码，下载后本地比对即可验证完整性。
      </p>

      <div className="res-toolbar">
        <input
          className="input"
          placeholder="搜索型号 / SKU / 名称"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="res-row">
          <span className="res-row-label">分类</span>
          <div className="res-tabs">
            {CAT_TABS.map((t) => (
              <button
                key={t.key}
                className={`tab ${cat === t.key ? 'active' : ''}`}
                onClick={() => setCat(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="res-row">
          <span className="res-row-label">类型</span>
          <div className="res-tabs">
            {KIND_TABS.map((t) => (
              <button
                key={t.key}
                className={`tab ${kind === t.key ? 'active' : ''}`}
                onClick={() => setKind(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="muted" style={{ margin: '6px 0 14px' }}>
        共 {items.length} 项资源{loading ? ' · 加载中…' : ''}
      </div>

      {items.length === 0 && !loading && (
        <p className="empty">没有匹配的资源，换个型号或品类试试。</p>
      )}

      <div className="res-grid">
        {items.map((it) => (
          <div className="os-card" key={it.id}>
            <div className="os-top">
              <span className="os-badge">
                <span className="os-dot" />
                {KIND_ICON[it.kind]} {KIND_LABEL[it.kind]}
              </span>
              <span className="muted">{it.product_sku} · {it.brand}</span>
            </div>
            <div className="res-title">{it.title}</div>
            <div className="os-meta">
              {it.os && <span>系统 <b>{it.os}</b></span>}
              {it.version && <span>版本 <b>v{it.version}</b></span>}
              {it.pages && <span>页数 <b>{it.pages}</b></span>}
              {it.resource_kind && (
                <span>类型 <b>{RES_LABEL[it.resource_kind] || it.resource_kind}</b></span>
              )}
              <span>大小 <b>{it.size_mb} MB</b></span>
              <span>发布 <b>{it.release_date}</b></span>
            </div>
            <div className="sha-box">SHA256: {it.sha256}</div>
            <div className="os-actions">
              <a
                className="btn primary sm"
                href={`${apiBase()}${it.file_url}`}
                download
                onClick={() =>
                  void memoryEvent('resource_download', it.product_sku, `${KIND_LABEL[it.kind]} ${it.title}`)
                }
              >
                ⬇ 下载
              </a>
              <button
                className={`btn ghost sm ${copied === it.id ? 'copy-ok' : ''}`}
                onClick={() => copySha(it.id, it.sha256)}
              >
                {copied === it.id ? '✓ 已复制' : '复制校验码'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
