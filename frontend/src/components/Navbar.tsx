import { NavLink } from 'react-router-dom'
import { useCart } from '../store'

export default function Navbar() {
  const { count } = useCart()
  return (
    <header className="navbar">
      <div className="container nav-inner">
        <NavLink to="/" className="brand">
          <span className="brand-mark">P4</span> 外设商城
        </NavLink>
        <nav className="nav-links">
          <NavLink to="/" end>首页</NavLink>
          <NavLink to="/products">全部商品</NavLink>
          <NavLink to="/drivers">资源下载</NavLink>
          <NavLink to="/assets">素材库</NavLink>
          <NavLink to="/webdriver">网页驱动</NavLink>
          <NavLink to="/flash">限时抢购</NavLink>
          <NavLink to="/workbench">工作台</NavLink>
          <NavLink to="/memory">记忆中心</NavLink>
          <NavLink to="/nlu">语义理解</NavLink>
          <NavLink to="/settings">设置</NavLink>
          <NavLink to="/checkout" className="cart-link">
            购物车 <span className="badge">{count}</span>
          </NavLink>
        </nav>
      </div>
    </header>
  )
}
