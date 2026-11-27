import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { CartLine, Product } from './types'

interface CartCtx {
  lines: CartLine[]
  add: (p: Product, qty?: number) => void
  remove: (id: string) => void
  clear: () => void
  total: number
  count: number
  buyerId: string
  setBuyerId: (id: string) => void
}

const Ctx = createContext<CartCtx | null>(null)
const BUYER_KEY = 'p4_buyer'
const CART_KEY = 'p4_cart'

function readBuyer(): string {
  try {
    return localStorage.getItem(BUYER_KEY) || 'buyer_demo'
  } catch {
    return 'buyer_demo'
  }
}

function readCart(): CartLine[] {
  try {
    const raw = localStorage.getItem(CART_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  // 用户身份与购物车都落在浏览器本地：刷新页面、关掉客服再回来，仍是「同一个用户」
  const [buyerId, setBuyerIdState] = useState<string>(readBuyer)
  const [lines, setLines] = useState<CartLine[]>(readCart)

  useEffect(() => {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(lines))
    } catch {
      /* 隐私模式下忽略 */
    }
  }, [lines])

  const setBuyerId = (id: string) => {
    const v = id.trim() || 'buyer_demo'
    try {
      localStorage.setItem(BUYER_KEY, v)
      localStorage.removeItem(CART_KEY)
    } catch {
      /* ignore */
    }
    setBuyerIdState(v)
    setLines([]) // 换用户 = 换一份记忆与购物车，避免串号
  }

  const add = (p: Product, qty = 1) =>
    setLines((prev) => {
      const found = prev.find((l) => l.product.id === p.id)
      if (found)
        return prev.map((l) => (l.product.id === p.id ? { ...l, qty: l.qty + qty } : l))
      return [...prev, { product: p, qty }]
    })

  const remove = (id: string) => setLines((prev) => prev.filter((l) => l.product.id !== id))
  const clear = () => setLines([])

  const total = lines.reduce((s, l) => s + l.product.price * l.qty, 0)
  const count = lines.reduce((s, l) => s + l.qty, 0)

  return (
    <Ctx.Provider value={{ lines, add, remove, clear, total, count, buyerId, setBuyerId }}>
      {children}
    </Ctx.Provider>
  )
}

export function useCart() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useCart 必须在 CartProvider 内使用')
  return c
}
