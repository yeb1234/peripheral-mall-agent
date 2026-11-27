import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Product } from '../types'
import { useCart } from '../store'
import { staticUrl } from '../api'

const CAT: Record<string, string> = {
  keyboard: '键盘', mouse: '鼠标', headset: '耳机', webcam: '摄像头', dock: '扩展坞', gamepad: '手柄',
}

export default function ProductCard({ product }: { product: Product }) {
  const navigate = useNavigate()
  const { add } = useCart()
  const [imgErr, setImgErr] = useState(false)
  return (
    <div className="card product-card">
      <div className="product-thumb">
        {imgErr ? (
          <span className="product-thumb-text">{product.name.slice(0, 2)}</span>
        ) : (
          <img
            src={staticUrl(product.image)}
            alt={product.name}
            loading="lazy"
            onError={() => setImgErr(true)}
          />
        )}
      </div>
      <div className="product-body">
        <span className="tag">{CAT[product.category] || product.category}</span>
        <h3 className="product-name">{product.name}</h3>
        <p className="product-brand">{product.brand}</p>
        <div className="product-foot">
          <span className="price-pill">¥{product.price}</span>
          <button className="btn ghost sm" onClick={() => navigate(`/products/${product.sku}`)}>
            查看
          </button>
          <button className="btn primary sm" onClick={() => add(product)}>
            加购
          </button>
        </div>
      </div>
    </div>
  )
}
