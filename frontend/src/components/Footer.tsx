import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="footer">
      <div className="footer-inner">
        <div>
          <div className="brand"><span className="brand-mark">P4</span> 外设商城</div>
          <p className="desc">
            专业外设电商 + 严谨多 Agent 客服演示。工具型 Agent 的生产级可靠性：
            身份 / 策略 / 二次确认 / 幂等 / 事务 / 审计，写操作全部可复盘。
          </p>
        </div>
        <div>
          <h4>选购</h4>
          <Link to="/products">全部商品</Link>
          <Link to="/drivers">资源下载</Link>
          <Link to="/checkout">购物车</Link>
        </div>
        <div>
          <h4>关于</h4>
          <Link to="/assets">产品资源库 · MCP</Link>
          <Link to="/workbench">客服工作台</Link>
          <Link to="/">首页</Link>
          <p>演示项目 · 仅用于学习</p>
        </div>
      </div>
      <div className="footer-bottom">© 2026 P4 外设商城 · 企业级 Agent Demo</div>
    </footer>
  )
}
