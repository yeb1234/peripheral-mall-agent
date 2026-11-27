import { useEffect, useMemo, useState } from 'react'
import { getProducts, staticUrl } from '../api'
import type { Product } from '../types'

/**
 * 网页驱动控制中心（演示用·安全可乱点）。
 *
 * 完全在前端运行、配置存 localStorage，不会真正写设备，也不会触发任何后端写操作。
 * 用来演示「网页驱动」这类硬件配置界面：DPI / 回报率 / 按键速度 / 模式 / LOD / 自定义按键。
 */

type BtnAction = '默认' | 'DPI 切换' | '回报率切换' | '多媒体(播放/暂停)' | '复制/粘贴' | '禁用'
const BTN_ACTIONS: BtnAction[] = ['默认', 'DPI 切换', '回报率切换', '多媒体(播放/暂停)', '复制/粘贴', '禁用']

interface DeviceCfg {
  dpi: number
  polling: number
  btnSpeed: number
  mode: '竞技' | '均衡' | '日常'
  motionSync: boolean
  lod: '低' | '中' | '高'
  buttons: Record<string, BtnAction>
}

const DEFAULT_CFG: DeviceCfg = {
  dpi: 1600,
  polling: 1000,
  btnSpeed: 2,
  mode: '竞技',
  motionSync: true,
  lod: '低',
  buttons: { 左键: '默认', 右键: '默认', 中键: '默认', 'DPI 键': 'DPI 切换', '侧键 1': '多媒体(播放/暂停)', '侧键 2': '复制/粘贴' },
}

const POLLINGS = [125, 250, 500, 1000, 2000, 4000, 8000]
const BTN_KEYS = ['左键', '右键', '中键', 'DPI 键', '侧键 1', '侧键 2']

const TABS = [
  { key: 'status', label: '当前状态' },
  { key: 'perf', label: 'DPI · 回报率 · 按键速度' },
  { key: 'mode', label: '模式 · LOD 状态' },
  { key: 'keys', label: '自定义按键' },
] as const
type TabKey = typeof TABS[number]['key']

function loadCfg(sku: string): DeviceCfg {
  try {
    const raw = localStorage.getItem(`p4-wc-${sku}`)
    if (raw) return { ...DEFAULT_CFG, ...JSON.parse(raw), buttons: { ...DEFAULT_CFG.buttons, ...JSON.parse(raw).buttons } }
  } catch { /* ignore */ }
  return DEFAULT_CFG
}

export default function WebDriver() {
  const [devices, setDevices] = useState<Product[]>([])
  const [sku, setSku] = useState<string>('')
  const [cfg, setCfg] = useState<DeviceCfg>(DEFAULT_CFG)
  const [tab, setTab] = useState<TabKey>('status')
  const [toast, setToast] = useState('')

  useEffect(() => {
    getProducts({ category: 'mouse' })
      .then((list) => { setDevices(list); if (list[0]) setSku(list[0].sku) })
      .catch(() => setDevices([]))
  }, [])

  useEffect(() => {
    if (sku) setCfg(loadCfg(sku))
  }, [sku])

  const device = useMemo(() => devices.find((d) => d.sku === sku), [devices, sku])
  const art = device?.image ? staticUrl(device.image) : staticUrl('/static/products/mouse.svg')

  const update = (patch: Partial<DeviceCfg>) => setCfg((c) => ({ ...c, ...patch }))
  const setBtn = (k: string, v: BtnAction) => setCfg((c) => ({ ...c, buttons: { ...c.buttons, [k]: v } }))

  // 当前状态页：把静态摘要变成「可以按着玩」的演示控件（不写设备）
  const cycleMode = () => {
    const order = ['竞技', '均衡', '日常'] as const
    const next = order[(order.indexOf(cfg.mode) + 1) % order.length]
    update({ mode: next })
    setToast(`已切换为「${next}」模式（演示，不会写设备）`)
    setTimeout(() => setToast(''), 1800)
  }
  const toggleMotionSync = () => {
    const next = !cfg.motionSync
    update({ motionSync: next })
    setToast(`Motion Sync 已${next ? '开启' : '关闭'}（演示）`)
    setTimeout(() => setToast(''), 1800)
  }
  const cycleLod = () => {
    const order = ['低', '中', '高'] as const
    const next = order[(order.indexOf(cfg.lod) + 1) % order.length]
    update({ lod: next })
    setToast(`工作高度 LOD：${next}（演示）`)
    setTimeout(() => setToast(''), 1800)
  }

  const save = () => {
    localStorage.setItem(`p4-wc-${sku}`, JSON.stringify(cfg))
    setToast('已保存到本地（模拟，不会真正写入设备）')
    setTimeout(() => setToast(''), 2200)
  }
  const reset = () => {
    localStorage.removeItem(`p4-wc-${sku}`)
    setCfg(DEFAULT_CFG)
    setToast('已恢复出厂设置')
    setTimeout(() => setToast(''), 2200)
  }

  return (
    <div className="wc">
      {/* 顶部：设备选择 + 状态 */}
      <div className="wc-top">
        <div className="wc-device-pick">
          <strong>网页驱动 · 控制中心</strong>
          <select value={sku} onChange={(e) => setSku(e.target.value)}>
            {devices.length === 0 && <option value="">（未加载到设备）</option>}
            {devices.map((d) => (
              <option key={d.sku} value={d.sku}>{d.name}</option>
            ))}
          </select>
        </div>
        <div className="wc-status-row">
          <span className="wc-battery">电量 <span className="bar"><i /></span> 100%</span>
          <span>固件 Ver.1.1</span>
          <span>程序版本 1.0.4</span>
          <span className="wc-theme">深色
            <button className="wc-switch" aria-label="主题切换（演示）" onClick={() => setToast('主题切换为演示按钮，可放心点') }><i /></button>
          </span>
        </div>
      </div>

      {/* Tab 栏 */}
      <div className="wc-tabs">
        {TABS.map((t) => (
          <button key={t.key} className={`wc-tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* 主体 */}
      <div className="wc-body">
        {/* 设备预览 + 数值标注 */}
        <div className="wc-preview">
          <img src={art} alt={device?.name || '设备'} />
          <div className="wc-callout tl">DPI<small>{cfg.dpi}</small></div>
          <div className="wc-callout tr">回报率<small>{cfg.polling}Hz</small></div>
          <div className="wc-callout bl">按键反应<small>{cfg.btnSpeed}ms</small></div>
          <div className="wc-callout br">模式<small>{cfg.mode}</small></div>
        </div>

        {/* 右侧面板 */}
        <div className="wc-panel">
          {tab === 'status' && (
            <>
              <h3>当前状态</h3>
              <p className="hint">下面是当前生效的配置摘要。<b>直接点下面的标签就能切换</b>（纯演示，不写设备）；点某条参数可跳到对应设置页继续调。</p>
              <div className="wc-chips">
                <button type="button" className="wc-chip live" onClick={cycleMode}>竞技模式：{cfg.mode}</button>
                <button type="button" className="wc-chip live" onClick={toggleMotionSync}>Motion Sync：{cfg.motionSync ? '开' : '关'}</button>
                <button type="button" className="wc-chip live" onClick={cycleLod}>工作高度：{cfg.lod}</button>
              </div>
              <div style={{ height: 14 }} />
              <Row label="DPI" sub="每英寸点数" value={String(cfg.dpi)} onClick={() => setTab('perf')} />
              <Row label="回报率" sub="Polling Rate" value={`${cfg.polling} Hz`} onClick={() => setTab('perf')} />
              <Row label="按键反应速度" sub="Button Speed" value={`${cfg.btnSpeed} ms`} onClick={() => setTab('perf')} />
            </>
          )}

          {tab === 'perf' && (
            <>
              <h3>DPI · 回报率 · 按键速度</h3>
              <p className="hint">拖动滑块或在分段里点选，数值会实时反映在左侧设备预览上。</p>
              <div className="wc-row">
                <div className="wc-label">DPI<small>100 – 5000，步进 50</small></div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <input className="wc-slider" type="range" min={100} max={5000} step={50}
                    value={cfg.dpi} onChange={(e) => update({ dpi: Number(e.target.value) })} />
                  <span className="wc-value">{cfg.dpi}</span>
                </div>
              </div>
              <div className="wc-row">
                <div className="wc-label">回报率<small>Polling Rate</small></div>
                <div className="wc-seg">
                  {POLLINGS.map((p) => (
                    <button key={p} className={cfg.polling === p ? 'active' : ''} onClick={() => update({ polling: p })}>{p}</button>
                  ))}
                </div>
              </div>
              <div className="wc-row">
                <div className="wc-label">按键反应速度<small>1 – 16 ms</small></div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <input className="wc-slider" type="range" min={1} max={16} step={1}
                    value={cfg.btnSpeed} onChange={(e) => update({ btnSpeed: Number(e.target.value) })} />
                  <span className="wc-value">{cfg.btnSpeed} ms</span>
                </div>
              </div>
            </>
          )}

          {tab === 'mode' && (
            <>
              <h3>模式 · LOD 状态</h3>
              <p className="hint">切换性能模式与传感器工作高度（LOD）。</p>
              <div className="wc-row">
                <div className="wc-label">性能模式</div>
                <div className="wc-seg">
                  {(['竞技', '均衡', '日常'] as const).map((m) => (
                    <button key={m} className={cfg.mode === m ? 'active' : ''} onClick={() => update({ mode: m })}>{m}</button>
                  ))}
                </div>
              </div>
              <div className="wc-row">
                <div className="wc-label">Motion Sync<small>降低抖动，提升追踪稳定</small></div>
                <button className={`wc-toggle ${cfg.motionSync ? 'on' : ''}`} onClick={() => update({ motionSync: !cfg.motionSync })}><i /></button>
              </div>
              <div className="wc-row">
                <div className="wc-label">工作高度 LOD<small>传感器离地高度</small></div>
                <div className="wc-seg">
                  {(['低', '中', '高'] as const).map((l) => (
                    <button key={l} className={cfg.lod === l ? 'active' : ''} onClick={() => update({ lod: l })}>{l}</button>
                  ))}
                </div>
              </div>
            </>
          )}

          {tab === 'keys' && (
            <>
              <h3>自定义按键</h3>
              <p className="hint">为各个物理按键分配功能，可随时改回「默认」。</p>
              <div className="wc-btn-list">
                {BTN_KEYS.map((k) => (
                  <div className="wc-btn-item" key={k}>
                    <span className="nm">{k}</span>
                    <select value={cfg.buttons[k] || '默认'} onChange={(e) => setBtn(k, e.target.value as BtnAction)}>
                      {BTN_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="wc-foot">
        <span className="wc-saved">配置仅保存在本浏览器（localStorage），刷新不丢；<b>随便点，不会写设备</b>。</span>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn ghost" onClick={reset}>恢复出厂</button>
          <button className="btn primary" onClick={save}>保存配置</button>
        </div>
      </div>

      <div className={`wc-toast ${toast ? 'show' : ''}`}>{toast}</div>
    </div>
  )
}

function Row({ label, sub, value, onClick }: { label: string; sub: string; value: string; onClick?: () => void }) {
  return (
    <div className={`wc-row${onClick ? ' is-click' : ''}`} onClick={onClick}>
      <div className="wc-label">{label}<small>{sub}</small></div>
      <div className="wc-right">
        <span className="wc-value">{value}</span>
        {onClick && <span className="wc-jump">调整 →</span>}
      </div>
    </div>
  )
}
