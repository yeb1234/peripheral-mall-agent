import { useEffect, useMemo, useState } from 'react'
import {
  assetUrl, getAssetBundle, getAssetManifest,
} from '../api'
import type { AssetBundle, AssetManifest } from '../api'

type Tab = 'shots' | 'copy' | 'specs' | 'images'

const TABS: { key: Tab; label: string }[] = [
  { key: 'shots', label: '短视频分镜' },
  { key: 'copy', label: '营销文案' },
  { key: 'specs', label: '产品参数' },
  { key: 'images', label: '图片素材' },
]

const SECTION_LABEL: Record<string, string> = {
  product_card: '产品卡图', category_visual: '16:9 品类主视觉', brand_hero: '品牌 Hero',
}

export default function Assets() {
  const [manifest, setManifest] = useState<AssetManifest | null>(null)
  const [bundle, setBundle] = useState<AssetBundle | null>(null)
  const [sku, setSku] = useState('')
  const [cat, setCat] = useState('')
  const [tab, setTab] = useState<Tab>('shots')
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState('')

  useEffect(() => {
    getAssetManifest()
      .then((m) => {
        setManifest(m)
        setSku(m.products[0]?.sku || '')
      })
      .catch((e: any) => setErr(e?.message || '读取资源库失败'))
  }, [])

  useEffect(() => {
    if (!sku) return
    setBundle(null)
    getAssetBundle(sku)
      .then(setBundle)
      .catch((e: any) => setErr(e?.message || '读取资产包失败'))
  }, [sku])

  const products = useMemo(
    () => (manifest?.products || []).filter((p) => !cat || p.category === cat),
    [manifest, cat],
  )

  const httpUrl = manifest?.mcp.http || 'http://127.0.0.1:8000/mcp'
  const mcpConfig = JSON.stringify({ mcpServers: { 'p4-assets': { url: httpUrl } } }, null, 2)

  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(label)
      window.setTimeout(() => setCopied(''), 1800)
    } catch {
      setErr('复制失败，请手动选中文本')
    }
  }

  if (!manifest) {
    return <div className="assets-page">{err || '正在读取 P4 产品资源库…'}</div>
  }

  return (
    <div className="assets-page">
      <div className="assets-head">
        <div>
          <div className="settings-kicker">ASSET LIBRARY / MCP</div>
          <h1>P4 产品资源库</h1>
          <p>
            参数、营销文案、图片与 30 秒短视频分镜，按 SKU 打包成自包含静态资产；
            通过 MCP 供出去，别的项目可直接读取，接着做宣传视频。
          </p>
        </div>
        <div className="assets-ver">
          <span className="assets-live-dot" />
          v{manifest.version} · {manifest.product_count} 个 SKU
          <small>{manifest.generated_at.replace('T', ' ').slice(0, 19)} UTC</small>
        </div>
      </div>

      <div className="assets-stats">
        <div><small>产品资产包</small><b>{manifest.product_count}</b></div>
        <div><small>品类</small><b>{manifest.categories.length}</b></div>
        <div><small>每款图片素材</small><b>3</b></div>
        <div><small>每款分镜</small><b>5 镜头 / 30s</b></div>
        <div><small>MCP 工具</small><b>{manifest.mcp.tools.length}</b></div>
      </div>

      <div className="assets-mcp">
        <div className="assets-mcp-head">
          <div>
            <h2>对外供出 · MCP</h2>
            <p>别的项目连下面任一端点即可读资源库，不用解析文件、不用连 P4 数据库。</p>
          </div>
          <button className="btn ghost sm" onClick={() => void copyText(mcpConfig, 'mcp.json 配置')}>
            复制客户端配置
          </button>
        </div>
        <div className="assets-endpoints">
          <div><small>Streamable HTTP（主站）</small><code>{manifest.mcp.http}</code></div>
          <div><small>独立 HTTP 端口</small><code>{manifest.mcp.http_standalone}</code></div>
          <div><small>stdio</small><code>{manifest.mcp.stdio}</code></div>
        </div>
        <div className="assets-tools">
          {manifest.mcp.tools.map((t) => <span key={t} className="assets-tool">{t}</span>)}
        </div>
        <details className="assets-config">
          <summary>查看 mcp.json 配置片段（HTTP 方式）</summary>
          <pre>{mcpConfig}</pre>
        </details>
      </div>

      <div className="assets-body">
        <aside className="assets-side">
          <div className="assets-side-head">
            <h3>产品</h3>
            <select className="input" value={cat} onChange={(e) => setCat(e.target.value)}>
              <option value="">全部品类</option>
              {manifest.categories.map((c) => (
                <option key={c.key} value={c.key}>{c.name}（{c.count}）</option>
              ))}
            </select>
          </div>
          <div className="assets-list">
            {products.map((p) => (
              <button
                key={p.sku}
                className={`assets-list-item ${p.sku === sku ? 'active' : ''}`}
                onClick={() => setSku(p.sku)}
              >
                <span className="assets-sku">{p.sku}</span>
                <b>{p.name}</b>
                <small>{p.tagline}</small>
              </button>
            ))}
            {!products.length && <div className="hint">该品类暂无产品</div>}
          </div>
        </aside>

        <section className="assets-detail">
          {!bundle ? (
            <div className="hint">正在读取 {sku} 资产包…</div>
          ) : (
            <>
              <div className="assets-detail-head">
                <div>
                  <div className="assets-kicker">{bundle.identity.category_cn} · {bundle.identity.brand}</div>
                  <h2>{bundle.identity.name}</h2>
                  <p>{bundle.copy.tagline} <span>¥{bundle.identity.price.toFixed(0)}</span></p>
                </div>
                <div className="assets-detail-meta">
                  <code>{bundle.sku}</code>
                  <small>{bundle.copy.video_script.duration_seconds}s · {bundle.copy.video_script.aspect_ratio}</small>
                </div>
              </div>

              <div className="assets-tabs">
                {TABS.map((t) => (
                  <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
                    {t.label}
                  </button>
                ))}
              </div>

              {tab === 'shots' && (
                <div className="assets-shots">
                  <div className="assets-note">
                    {bundle.copy.video_script.style} · BGM：{bundle.copy.video_script.bgm_hint}
                  </div>
                  {bundle.copy.video_script.shots.map((s) => (
                    <div className="assets-shot" key={s.no}>
                      <div className="assets-shot-no">{String(s.no).padStart(2, '0')}<small>{s.timecode}</small></div>
                      <div className="assets-shot-body">
                        <p className="assets-shot-visual">{s.visual}</p>
                        <div className="assets-shot-line"><em>字幕</em><span>{s.overlay_text}</span></div>
                        <div className="assets-shot-line"><em>口播</em><span>{s.voiceover}</span></div>
                        <div className="assets-shot-ref">素材：{SECTION_LABEL[s.asset_ref] || s.asset_ref}</div>
                      </div>
                    </div>
                  ))}
                  <div className="assets-note">{bundle.copy.video_script.cta}</div>
                  <div className="assets-note muted">{bundle.copy.video_script.compliance_note}</div>
                </div>
              )}

              {tab === 'copy' && (
                <div className="assets-copy">
                  <div className="assets-block">
                    <h4>短文案</h4><p>{bundle.copy.short_copy}</p>
                  </div>
                  <div className="assets-block">
                    <h4>长文案</h4><p className="pre">{bundle.copy.long_copy}</p>
                  </div>
                  <h4>卖点（参数来自商品库）</h4>
                  <div className="assets-points">
                    {bundle.copy.selling_points.map((p) => (
                      <div key={p.key} className="assets-point">
                        <b>{p.headline}</b><span>{p.benefit}</span>
                      </div>
                    ))}
                  </div>
                  <h4>使用场景</h4>
                  <div className="assets-chips">
                    {bundle.copy.scenes.map((s) => <span key={s.scene}>{s.scene}</span>)}
                  </div>
                  <h4>关键词</h4>
                  <div className="assets-chips">
                    {bundle.copy.keywords.map((k) => <span key={k}>{k}</span>)}
                  </div>
                </div>
              )}

              {tab === 'specs' && (
                <div className="assets-specs">
                  <p className="hint">specs_flat 可直接用于字幕 / 参数卡；下方为压平后的全部 {Object.keys(bundle.specs_flat).length} 项。</p>
                  <table>
                    <tbody>
                      {Object.entries(bundle.specs_flat).map(([k, v]) => (
                        <tr key={k}><th>{k}</th><td>{v}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {tab === 'images' && (
                <div className="assets-images">
                  {Object.entries(bundle.images).map(([key, img]) => (
                    <div className="assets-image" key={key}>
                      <div className="assets-image-preview">
                        <img src={assetUrl(img.path)} alt={img.purpose} loading="lazy" />
                      </div>
                      <div className="assets-image-meta">
                        <b>{SECTION_LABEL[key] || key}</b>
                        <small>{img.width}×{img.height} · {img.mime_type}</small>
                        <small>{img.purpose}</small>
                        <code>{img.path}</code>
                        <a href={assetUrl(img.path)} target="_blank" rel="noreferrer">打开原图 ↗</a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {err && <div className="settings-result bad">{err}</div>}
      {copied && <div className="assets-toast">已复制 {copied}</div>}
    </div>
  )
}
