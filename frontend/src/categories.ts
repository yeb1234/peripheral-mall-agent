// 分类展示元数据：key 来自后端 DB（/api/products/categories），
// 图标 / 中文名 / 副标题是前端展示层配置。新增品类后端自动出现，走默认展示。
export interface CatMeta {
  icon: string
  name: string
  sub: string
}

export const CAT_META: Record<string, CatMeta> = {
  keyboard: { icon: '⌨️', name: '机械键盘', sub: '磁轴 / 客制化' },
  mouse: { icon: '🖱️', name: '游戏鼠标', sub: '大手 / 中小手' },
  headset: { icon: '🎧', name: '电竞耳机', sub: 'ANC / 7.1 声道' },
  webcam: { icon: '📷', name: '高清摄像头', sub: '4K / 自动追焦' },
  dock: { icon: '🔌', name: '扩展坞', sub: '雷电 / 多屏' },
  gamepad: { icon: '🎮', name: '游戏手柄', sub: '霍尔 / 体感' },
}

export const CAT_ORDER = ['keyboard', 'mouse', 'headset', 'webcam', 'dock', 'gamepad']

export function catLabel(key: string): string {
  return CAT_META[key]?.name || key
}
