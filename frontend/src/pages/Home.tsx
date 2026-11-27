import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getProducts, getCategories, staticUrl } from '../api'
import ProductCard from '../components/ProductCard'
import type { Product } from '../types'
import { CAT_META, CAT_ORDER } from '../categories'

export default function Home() {
  const [all, setAll] = useState<Product[]>([])
  const [cats, setCats] = useState<{ key: string; icon: string; name: string; sub: string }[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getProducts({})
      .then(setAll)
      .catch(() => setAll([]))
      .finally(() => setLoading(false))
    // 分类列表以后端 DB 为准
    getCategories()
      .then((rows) => {
        const keys = rows.map((r) => r.key)
        keys.sort((a, b) => CAT_ORDER.indexOf(a) - CAT_ORDER.indexOf(b))
        setCats(
          keys.map((k) => {
            const m = CAT_META[k] || { icon: '🛒', name: k, sub: '' }
            return { key: k, ...m }
          }),
        )
      })
      .catch(() => setCats([]))
  }, [])

  const featured = all.filter((p) => p.featured).slice(0, 8)
  // 新品插页：挑一款无线鼠标作为「今晚 19:00 抢购」预告
  const arrival =
    all.find((p) => p.category === 'mouse' && p.name.includes('无线')) || all[0]

  return (
    <div className="home">
      {/* 顶部公告条 */}
      <div className="promo-bar">
        <span className="dot">●</span> 今晚 <b>19:00</b> 限量抢购 · 新款无线鼠标预售排队火爆{' '}
        <Link to="/flash">立即查看 →</Link>
      </div>

      {/* AI 生成主视觉（保留展示） */}
      <section className="hero-showcase">
        <div className="hs-copy">
          <div className="b-label">P4 · Pro 电竞外设</div>
          <h1 className="hs-title">为胜利而生的<br />专业外设装备</h1>
          <p className="hs-sub">
            鼠标 · 键盘 · 耳机，由多 Agent 全程护航的售前推荐、售中下单与售后体验。
          </p>
          <div className="hs-actions">
            <Link to="/products" className="btn primary">浏览全部商品 →</Link>
            <Link to="/webdriver" className="btn ghost">网页驱动控制中心</Link>
          </div>
        </div>
        <div className="hs-visual">
          <img src={staticUrl('/static/hero.png')} alt="P4 专业电竞外设" />
        </div>
      </section>

      {/* Hero：两栏 Banner（VAXEE 风格） */}
      <section className="hero-banners">
        <Link to="/products" className="banner esports">
          <div>
            <div className="b-label">Esports · 专业电竞</div>
            <div className="b-title">电竞外设装备</div>
            <div className="b-sub">鼠标 · 键盘 · 耳机 · 手柄，为胜利而生。</div>
            <span className="btn primary">浏览全部商品 →</span>
          </div>
          <img className="b-art" src={staticUrl('/static/products/mouse.svg')} alt="" />
        </Link>
        <Link to="/webdriver" className="banner driver">
          <div>
            <div className="b-label">Web Driver · 网页驱动</div>
            <div className="b-title">在线调校你的设备</div>
            <div className="b-sub">DPI · 回报率 · 自定义按键，浏览器里点一点就好。</div>
            <span className="btn primary">进入控制中心 →</span>
          </div>
          <img className="b-art" src={staticUrl('/static/products/keyboard.svg')} alt="" />
        </Link>
      </section>

      {/* 品类快捷导航（分类来源：后端 DB） */}
      <div className="cat-grid">
        {cats.map((c) => (
          <Link to="/products" className="cat-tile" key={c.key}>
            <div className="cat-ico">{c.icon}</div>
            <div className="cat-name">{c.name}</div>
            <div className="cat-sub">{c.sub}</div>
          </Link>
        ))}
      </div>

      {/* 新品插页（news 风格） */}
      {arrival && (
        <section className="arrivals">
          <div className="ghost-title">NEW ARRIVALS</div>
          <div className="arrivals-head">
            <span className="badge-new"><span className="b-dot" />新品首发</span>
            <h2>新品推荐</h2>
            <span className="en">Latest Drop</span>
          </div>
          <div className="arrival-card">
            <div className="arrival-thumb">
              <img src={staticUrl(arrival.image)} alt={arrival.name} />
            </div>
            <div className="arrival-info">
              <h3>{arrival.name}</h3>
              <p>
                今晚 <span className="count">19:00</span> 开启抢购，预售排队火爆。
                霍尔磁感 / 8K 回报率 / 轻量化机身，专业选手同款手感，售前售后由多 Agent 全程护航。
              </p>
            </div>
            <Link to={`/products/${arrival.sku}`} className="btn primary">查看详情 →</Link>
          </div>
        </section>
      )}

      {/* 热门推荐 */}
      <section>
        <h2 className="section-title">热门推荐 <span className="line" /></h2>
        {loading ? (
          <div className="grid">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="card product-card skeleton skel-card" />
            ))}
          </div>
        ) : (
          <div className="grid">
            {featured.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
            {featured.length === 0 && (
              <p className="empty">暂无推荐商品（请确认后端已启动并建表）。</p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
