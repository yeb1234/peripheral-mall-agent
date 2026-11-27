/**
 * 回归测试：React.StrictMode 会把 setState 的 updater 调用两次。
 * 验证 src/lib/chatMsgs.ts 的纯函数在双调用下不会把 token 追加两次
 * （即不会出现「KKBB--PPRROO8877 是是否否支支持持」这种逐字重复）。
 *
 * 运行： node scripts/check-chat-msgs.cjs
 */
const esbuild = require('esbuild')
const fs = require('fs')
const path = require('path')

const out = path.join(__dirname, '.chatMsgs.generated.cjs')
esbuild.buildSync({
  entryPoints: [path.join(__dirname, '..', 'src', 'lib', 'chatMsgs.ts')],
  outfile: out,
  bundle: true,
  platform: 'node',
  format: 'cjs',
  logLevel: 'silent',
})
const M = require(out)
fs.unlinkSync(out)

let failed = 0
function check(name, cond, extra) {
  if (cond) console.log('  PASS  ' + name)
  else {
    console.log('  FAIL  ' + name, extra === undefined ? '' : extra)
    failed++
  }
}

// 1) 复现旧实现：mutation + StrictMode 双调用 → 字符翻倍
console.log('[1] 旧实现（直接 mutate state）在 StrictMode 下')
const oldState = [
  { role: 'user', content: 'KB-PRO87 支持 macOS 吗' },
  { role: 'assistant', content: '' },
]
function oldAppend(state, ch) {
  const c = [...state] // 浅拷贝数组，元素对象仍是同一引用
  const last = c[c.length - 1]
  if (last.role === 'assistant') last.content += ch
  return c
}
const oldFirst = oldAppend(oldState, 'K')
oldAppend(oldState, 'K') // StrictMode 第二次调用，传入的还是同一个 state
check('旧实现确实产生 "KK"（复现截图里的逐字重复）', oldFirst[1].content === 'KK',
  '实际=' + JSON.stringify(oldFirst[1].content))

// 2) 新实现：双调用结果一致、原 state 不被污染
console.log('[2] 新实现（不可变纯函数）在 StrictMode 下')
const s0 = [{ role: 'user', content: 'q' }, { role: 'assistant', content: '' }]
const a = M.appendToken(s0, 'K')
const b = M.appendToken(s0, 'K')
check('原 state 未被修改', s0[1].content === '')
check('两次调用结果都是 "K"', a[1].content === 'K' && b[1].content === 'K',
  JSON.stringify([a[1].content, b[1].content]))

// 3) 逐 token 模拟完整一轮流式回复，全程双调用
const reply = 'KB-PRO87 支持 macOS'
let st = [{ role: 'user', content: 'q' }, { role: 'assistant', content: '' }]
let strictOk = true
for (const ch of reply) {
  const r1 = M.appendToken(st, ch)
  const r2 = M.appendToken(st, ch)
  if (r1[1].content !== r2[1].content) strictOk = false
  st = r1
}
check('逐 token 双调用结果始终一致', strictOk)
check('拼接结果与原文完全一致（无重复字符）', st[1].content === reply,
  '实际=' + JSON.stringify(st[1].content))

// 4) 排队占位：首个 token 到达时清除，且不重复
console.log('[3] 排队占位 / 错误 / 兜底')
let q = [{ role: 'user', content: 'q' }, { role: 'assistant', content: '' }]
q = M.setQueueHint(q, 2)
check('排队提示已显示', q[1].queued === true && q[1].content.includes('排队'))
const qa = M.appendToken(q, '关')
const qb = M.appendToken(q, '关')
check('首个 token 清掉占位且不重复', qa[1].content === '关' && qb[1].content === '关' && qa[1].queued === false,
  JSON.stringify([qa[1].content, qb[1].content]))

const errState = [{ role: 'user', content: 'q' }, { role: 'assistant', content: '半截' }]
const e1 = M.setError(errState, '[LLM001] 限流')
check('错误覆盖气泡且不污染原 state',
  e1[1].content === '[LLM001] 限流' && e1[1].error === true && errState[1].content === '半截')

const blank = M.ensureNotBlank([{ role: 'user', content: 'q' }, { role: 'assistant', content: '' }])
check('空气泡兜底为 SYS000 提示', blank[1].content.startsWith('[SYS000]') && blank[1].error === true)
const normal = [{ role: 'user', content: 'q' }, { role: 'assistant', content: '正常回复' }]
check('正常回复不会被兜底覆盖', M.ensureNotBlank(normal) === normal)

const metaState = [{ role: 'user', content: 'q' }, { role: 'assistant', content: 'OK' }]
const mm = M.applyMeta(metaState, { session_id: 's', need_bind: true })
check('meta 标记写入且保留正文', mm[1].needBind === true && mm[1].content === 'OK')

console.log(failed === 0 ? '\nALL CHECKS PASSED' : '\n' + failed + ' CHECK(S) FAILED')
process.exit(failed === 0 ? 0 : 1)
