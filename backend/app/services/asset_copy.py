"""
产品营销文案与短视频分镜生成（确定性、可复现、零模型调用）。

为什么不用 LLM 生成：资源库是要交给「别的项目」做宣传视频的**输入资产**，
必须稳定、可复现、可版本管理，且事实严格来自商品库的真实参数，不能让模型
临时编一个不存在的 8K 回报率。所以这里用「品类模板 + 真实规格值」拼装，
生成脚本可重复跑，diff 干净，也方便人工改模板后整库重建。

对外交付的 copy 结构（每个 SKU）：
  tagline / short_copy / long_copy / selling_points / scenes / video_script / keywords
其中 video_script 是 5 镜头 16:9 分镜（画面 + 字幕 + 口播 + 素材引用），
视频项目可直接拿去排期，不必再猜产品卖点。
"""
from __future__ import annotations

from typing import Any

CATEGORY_CN = {
    "keyboard": "机械键盘", "mouse": "电竞鼠标", "headset": "游戏耳机",
    "webcam": "高清摄像头", "dock": "扩展坞", "gamepad": "游戏手柄",
}

# 品牌视觉基调：浅色专业 gaming gear（VAXEE 风），文案克制、参数说话
BRAND = {
    "name": "P4",
    "slogan": "为每一次操作负责",
    "tone": "克制、专业、参数说话；不堆形容词，不承诺未标注的性能",
    "palette": {"ink": "#15161a", "surface": "#f5f6f8", "accent": "#d92626", "muted": "#8a8f98"},
    "keywords": ["专业外设", "电竞", "参数透明", "可下载驱动与说明书"],
}

# 每个品类：主标语、卖点字段（从真实 specs 取值 + 利益点）、场景、5 镜头分镜
_CATEGORY: dict[str, dict[str, Any]] = {
    "keyboard": {
        "tagline": "手感，由你定义。",
        "lead": "把配列、轴体与触发都交回给你自己",
        "highlights": [
            ("layout", "配列", "桌面留出更多鼠标空间，视线更聚焦"),
            ("switch_kind", "轴体", "每一次触发都干净利落，长时间输入不累手"),
            ("hotswap", "轴座", "换轴不焊接，风格随时改"),
            ("polling_hz", "回报率", "高频回报把输入延迟压到感知之外"),
            ("rgb", "灯效", "桌面氛围自己定，也可以一键关灯"),
            ("structure", "结构", "抑制空腔音，敲击声更纯"),
        ],
        "scenes": ["深夜单排 FPS 的紧凑桌面", "开放式办公区的长时间码字", "周末自己动手换轴"],
        "shots": [
            ("0-4", "俯拍桌面全景：浅灰桌垫、键盘居中、鼠标留出大面积空间；柔光侧打",
             "{layout} 配列，桌面刚刚好", "{name}。把空间还给鼠标，也把注意力还给屏幕。", "category_visual"),
            ("4-10", "微距推近轴体与键帽，手指连续敲击三下，慢动作捕捉回弹",
             "{switch_kind}", "轴体决定手感，热插拔让你随时改变主意。", "product_card"),
            ("10-17", "侧逆光扫过铝合金上盖与 PBT 键帽纹理，镜头缓慢平移",
             "{top_material} · {keycap_material}", "看得见的结构，听得见的克制。", "category_visual"),
            ("17-24", "驱动界面录屏：调触发行程 / 灯效 / 保存板载，UI 快切三格",
             "官方驱动可调，配置保存到板载", "参数不是出厂就锁死的，{software} 里都能改。", "product_card"),
            ("24-30", "回到桌面全景，屏幕亮起游戏画面，键盘灯效与画面同步；右下角出现 SKU 与品牌字标",
             "{name} · {sku}", "{brand} {tagline} 官网资源页可下载驱动与完整说明书。", "brand_hero"),
        ],
        "bgm": "低频电子 / 70-90 BPM / 干净无鼓点堆叠，敲击声做拟音重点",
    },
    "mouse": {
        "tagline": "每一次移动，都算数。",
        "lead": "重量、握感与追踪，只服务于准星",
        "highlights": [
            ("weight_g", "重量", "轻量化让长时间甩枪不拖手腕"),
            ("sensor", "传感器", "高 IPS 与加速度，快速转身不丢帧"),
            ("dpi", "DPI", "档位随手感切换，FPS 与 MOBA 各一套"),
            ("polling_hz", "回报率", "高频轮询让光标跟手"),
            ("connect", "连接", "有线 / 2.4G / 蓝牙按需切换"),
            ("shape", "模具", "贴合手型，趴握抓握都稳"),
        ],
        "scenes": ["FPS 训练场连续甩枪", "长时间 MOBA 对线", "出差带着笔记本轻量办公"],
        "shots": [
            ("0-4", "低机位贴桌推轨：鼠标在浅色布垫上快速划过，留一点运动模糊",
             "{weight_g} · 轻量化", "{name}。移动之前，先让手腕没有负担。", "category_visual"),
            ("4-10", "俯拍手掌握持特写，切换趴握 / 抓握两种姿势",
             "{shape}", "模具跟着手型走，不是手去迁就模具。", "product_card"),
            ("10-17", "屏幕录制准星急停点射，画中画叠传感器参数字幕",
             "{sensor} · {dpi}", "追踪稳，急停才停得住。", "category_visual"),
            ("17-24", "切换有线 / 2.4G / 蓝牙三个连接状态灯，快切三格",
             "{connect}", "桌面、赛场、出差，一台就够。", "product_card"),
            ("24-30", "定格产品侧影 + 品牌字标，右下角 SKU",
             "{name} · {sku}", "{brand} {tagline} 驱动与说明书在资源下载中心。", "brand_hero"),
        ],
        "bgm": "极简电子脉冲 / 100 BPM / 甩枪瞬间留白一拍",
    },
    "headset": {
        "tagline": "只留下该听见的声音。",
        "lead": "定位、语音与佩戴，一场比赛都不掉线",
        "highlights": [
            ("anc", "降噪", "把环境噪声挡在耳罩外，只留脚步"),
            ("driver", "单元", "声场开阔，方位感更清楚"),
            ("surround", "环绕", "虚拟多声道让前后左右可辨"),
            ("mic", "麦克风", "通话清晰，指挥不重复第二遍"),
            ("connect", "连接", "低延迟无线与蓝牙可同时待机"),
            ("battery", "续航", "一次充电覆盖整周训练"),
        ],
        "scenes": ["深夜排位不想吵到家人", "开黑语音指挥", "通勤路上切到手机听歌"],
        "shots": [
            ("0-4", "暗环境侧光：耳罩轮廓缓缓亮起，环境音波形被「压平」的图形动画",
             "{anc}", "{name}。先安静下来，再谈听声辨位。", "category_visual"),
            ("4-10", "耳机佩戴特写，头梁与耳罩贴合，镜头绕半圈",
             "{driver}", "包耳不夹头，戴满一整场也不想摘。", "product_card"),
            ("10-17", "游戏画面录屏 + 脚步声方位指示环，字幕标出前后左右",
             "{surround}", "脚步从哪来，耳朵先于眼睛知道。", "category_visual"),
            ("17-24", "麦克风杆翻下/上翻的静音动作，语音波形随之消失",
             "{mic} · 上翻静音", "一个动作就能闭麦，不用在软件里找按钮。", "product_card"),
            ("24-30", "桌面定格，耳机灯效与屏幕同步，品牌字标淡入",
             "{name} · {sku}", "{brand} {tagline} 官方驱动与说明书可下载。", "brand_hero"),
        ],
        "bgm": "空间感氛围铺底 / 脚步与语音做前景拟音",
    },
    "webcam": {
        "tagline": "让画面替你说清楚。",
        "lead": "会议、直播与录课，开箱即用",
        "highlights": [
            ("resolution", "分辨率", "细节清楚，白板文字也读得出"),
            ("fps", "帧率", "口型与动作不脱节"),
            ("focus", "对焦", "走近走远都保持清晰"),
            ("mic", "麦克风", "不用额外再架一支麦"),
            ("privacy", "隐私", "物理遮挡盖，不开会就盖上"),
        ],
        "scenes": ["远程周会共享方案", "在家直播或录课", "面试前的画面检查"],
        "shots": [
            ("0-4", "显示器上沿特写：镜头卡扣扣上，指示灯亮起",
             "{resolution} · 即插即用", "{name}。插上就能开始，不必先装驱动。", "category_visual"),
            ("4-10", "画面由模糊到清晰的对焦动画，字幕叠参数",
             "{focus}", "人一动，焦点也跟着走。", "product_card"),
            ("10-17", "分屏：会议窗口里的人物特写 + 白板文字可辨",
             "{resolution} · {fps}", "脸清楚，写的东西也清楚。", "category_visual"),
            ("17-24", "手指拨上物理遮挡盖，指示灯熄灭",
             "{privacy}", "会议结束，画面就该彻底关掉。", "product_card"),
            ("24-30", "桌面全景定格 + 品牌字标与 SKU",
             "{name} · {sku}", "{brand} {tagline} 固件与说明书在资源下载中心。", "brand_hero"),
        ],
        "bgm": "轻快干净的原声吉他 / 90 BPM",
    },
    "dock": {
        "tagline": "一根线，收好整张桌面。",
        "lead": "显示、供电、网口与读卡，全部归到一处",
        "highlights": [
            ("ports_total", "接口数量", "外设一次接齐，不再反复插拔"),
            ("video", "视频输出", "外接显示器即插即亮"),
            ("max_res", "最高分辨率", "高刷与高分辨率都留有余量"),
            ("pd_w", "反向供电", "一根线同时给笔记本充电"),
            ("ethernet", "网口", "有线网络更稳，会议不掉线"),
        ],
        "scenes": ["工位一根线切换笔记本", "双屏剪辑或看盘", "会议室投屏"],
        "shots": [
            ("0-4", "俯拍凌乱桌面 → 一根 Type-C 插入，线缆与外设依次「归位」的定格动画",
             "{ports_total}", "{name}。桌面乱，多半只是接口不够用。", "category_visual"),
            ("4-10", "接口侧面巡览：HDMI / DP / 网口 / 读卡器逐个点亮",
             "{video} · {ethernet}", "该有的口，一次给齐。", "product_card"),
            ("10-17", "双屏点亮，窗口从笔记本拖到外接显示器",
             "{max_res}", "屏幕多了，桌面反而更干净。", "category_visual"),
            ("17-24", "笔记本电量图标上升动画，字幕标供电功率",
             "{pd_w} 反向供电", "显示和充电，同一根线就够。", "product_card"),
            ("24-30", "收纳完成的桌面定格 + 品牌字标与 SKU",
             "{name} · {sku}", "{brand} {tagline} 规格与说明书可在资源下载中心查到。", "brand_hero"),
        ],
        "bgm": "克制的环境电子 / 每次「归位」给一个轻点击音",
    },
    "gamepad": {
        "tagline": "手感对了，才轮到操作。",
        "lead": "跨平台、可映射、低延迟，一把打通",
        "highlights": [
            ("connect", "连接", "主机 / PC / 手机按模式切换"),
            ("sticks", "摇杆", "死区可调，漂移可校准"),
            ("triggers", "扳机", "线性行程，赛车与射击各取所需"),
            ("back_buttons", "背键", "多两个不用离手的操作"),
            ("compat", "兼容平台", "一台手柄覆盖常用设备"),
        ],
        "scenes": ["客厅主机上的赛车游戏", "PC 上用手柄玩动作游戏", "手机串流临时来一局"],
        "shots": [
            ("0-4", "暗背景产品旋转，灯条亮起，模式开关被拨动",
             "{connect}", "{name}。设备换了，手柄不用换。", "category_visual"),
            ("4-10", "拇指推摇杆的微距慢动作，叠死区调节 UI",
             "{sticks}", "死区多大，由你的手决定。", "product_card"),
            ("10-17", "扳机扣到底的侧拍，字幕标行程档位",
             "{triggers}", "线性扳机，油门与开火都能拿捏。", "category_visual"),
            ("17-24", "背键映射界面录屏，两枚背键点亮",
             "{back_buttons}", "常用动作挪到手背，拇指不用离开摇杆。", "product_card"),
            ("24-30", "手柄定格 + 品牌字标与 SKU",
             "{name} · {sku}", "{brand} {tagline} 驱动与说明书可下载。", "brand_hero"),
        ],
        "bgm": "节奏明确的电子鼓点 / 110 BPM",
    },
}


def _flat_specs(specs: Any) -> dict[str, str]:
    """分组规格压平成 {属性: 值}，供文案模板按 key 取值。"""
    out: dict[str, str] = {}

    def fmt(v) -> str:
        if isinstance(v, list):
            return "、".join(map(str, v))
        if isinstance(v, bool):
            return "支持" if v else "不支持"
        return str(v)

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    if v and not isinstance(v[0], dict):
                        out[str(k)] = fmt(v)
                else:
                    out[str(k)] = fmt(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(specs)
    return out


def _pick(flat: dict[str, str], keys: list[str]) -> str:
    """按候选 key 逐个找；支持模糊包含（不同品类的规格字段名不完全一致）。"""
    for key in keys:
        if key in flat:
            return flat[key]
    for key in keys:
        for k, v in flat.items():
            if key.lower() in k.lower():
                return v
    return ""


# 模板变量 → 规格候选 key（跨品类字段名差异在这里吸收）
_ALIASES: dict[str, list[str]] = {
    "layout": ["配列", "layout", "键数", "keys"],
    "switch_kind": ["轴体类型", "轴体", "switch_kind", "轴"],
    "hotswap": ["热插拔", "hotswap", "轴座"],
    "polling_hz": ["回报率", "polling_hz", "轮询率", "回报率Hz"],
    "rgb": ["灯效", "RGB", "rgb", "背光"],
    "structure": ["结构", "structure"],
    "top_material": ["上盖材质", "top_material", "外壳", "材质"],
    "keycap_material": ["键帽材质", "keycap_material", "键帽"],
    "software": ["驱动软件", "software", "软件", "固件"],
    "weight_g": ["重量", "weight_g", "重量g"],
    "sensor": ["传感器", "sensor", "引擎"],
    "dpi": ["最高 DPI", "最高DPI", "dpi", "DPI", "分辨率档位"],
    "shape": ["适用手型", "适用手", "shape", "模具", "人体工学"],
    "connect": ["连接方式", "connect", "连接", "接口"],
    "anc": ["降噪", "ANC", "anc", "主动降噪"],
    "driver": ["单元", "driver", "驱动单元", "发声单元"],
    "surround": ["环绕", "surround", "声道", "7.1", "声场"],
    "mic": ["麦克风", "mic", "拾音", "通话降噪", "ENC", "指向性", "AI 防风噪"],
    "battery": ["续航", "battery", "电池"],
    "resolution": ["分辨率", "resolution", "最高分辨率"],
    "fps": ["帧率", "fps", "帧"],
    "focus": ["对焦", "focus"],
    "privacy": ["隐私", "遮挡", "privacy"],
    "ports_total": ["接口总数", "ports_total", "接口数量", "端口"],
    "video": ["视频输出", "video", "显示输出"],
    "max_res": ["最大分辨率", "max_res", "最高分辨率", "输出分辨率"],
    "pd_w": ["PD", "pd_w", "供电", "反向供电"],
    "ethernet": ["网口", "ethernet", "以太网"],
    "sticks": ["摇杆", "sticks", "摇杆类型"],
    "triggers": ["扳机", "triggers", "扳机行程"],
    "back_buttons": ["背键", "back_buttons", "自定义键"],
    "compat": ["兼容", "compat", "支持平台", "兼容系统"],
}


# 规格值缺失/为占位符时的**描述性**兜底：不含数字、不构成性能承诺，
# 只用于让字幕不出现突兀的「—」。事实性参数仍一律来自真实 specs。
_FALLBACK = {
    "layout": "紧凑配列", "switch_kind": "机械轴", "hotswap": "可换轴",
    "polling_hz": "高回报率", "rgb": "可自定义灯效", "structure": "扎实结构",
    "top_material": "金属质感", "keycap_material": "耐用键帽", "software": "官方驱动",
    "weight_g": "轻量机身", "sensor": "高精度传感器", "dpi": "可调 DPI",
    "shape": "人体工学模具", "connect": "多模连接",
    "anc": "沉浸式听感", "driver": "大尺寸单元", "surround": "精准声场",
    "mic": "清晰通话", "battery": "长续航",
    "resolution": "高清画质", "fps": "流畅画面", "focus": "快速对焦", "privacy": "物理遮挡",
    "ports_total": "丰富接口", "video": "视频输出", "max_res": "高分辨率输出",
    "pd_w": "反向供电", "ethernet": "有线网口",
    "sticks": "可调死区摇杆", "triggers": "线性扳机", "back_buttons": "自定义背键",
    "compat": "多平台兼容",
}
_EMPTY_MARKS = {"", "—", "-", "无", "不支持", "0 h", "0 ms", "0"}


def _vars(product: dict, flat: dict[str, str], cat: dict) -> dict[str, str]:
    v = {
        "sku": product.get("sku", ""),
        "name": product.get("name", ""),
        "brand": product.get("brand", "P4"),
        "tagline": cat["tagline"],
        "price": f"¥{product.get('price', 0):.0f}",
    }
    for key, aliases in _ALIASES.items():
        val = _pick(flat, aliases)
        v[key] = _FALLBACK.get(key, "—") if val.strip() in _EMPTY_MARKS else val
    return v


def _fill(template: str, v: dict[str, str]) -> str:
    out = template
    for key, val in v.items():
        out = out.replace("{" + key + "}", val)
    # 没匹配到的占位不留在成品里
    import re as _re
    return _re.sub(r"\{[a-z_]+\}", "—", out)


def build_copy(product: dict, specs: dict) -> dict:
    """单个 SKU → 完整营销文案包（事实全部来自商品库）。"""
    cat_key = product.get("category", "keyboard")
    cat = _CATEGORY.get(cat_key, _CATEGORY["keyboard"])
    flat = _flat_specs(specs)
    v = _vars(product, flat, cat)
    desc = (product.get("description") or "").strip()
    cat_cn = CATEGORY_CN.get(cat_key, cat_key)

    tagline = cat["tagline"]
    short_copy = f"{product['name']}｜{cat['lead']}。{desc}"
    long_copy = (
        f"{product['name']} 是 {product.get('brand', 'P4')} 面向{category_audience(cat_key)}推出的{cat_cn}。"
        f"{desc}\n"
        f"我们不把参数藏在宣传页脚注里：{selling_brief(flat, cat)}。"
        f"从连接方式到驱动软件，完整规格都可以在商品页与资源下载中心查到；"
        f"驱动、用户手册与快速入门支持按型号检索下载，下载文件提供 SHA256 校验。\n"
        f"{product.get('brand', 'P4')} {tagline}"
    ).replace("：，", "：")

    selling_points = []
    for key, label, benefit in cat["highlights"]:
        # 卖点的 fact 必须是 DB 里的真实参数；缺失就跳过，不用描述性兜底冒充规格
        val = _pick(flat, _ALIASES.get(key, [key]))
        if val.strip() in _EMPTY_MARKS:
            continue
        selling_points.append({
            "key": key, "label": label, "fact": val,
            "headline": f"{label} · {val}",
            "benefit": benefit,
            "copy": f"{val}｜{benefit}",
        })
        if len(selling_points) >= 5:
            break
    # 至少补一条兜底，保证每个 SKU 都有可用卖点
    if not selling_points:
        selling_points.append({"key": "description", "label": "产品定位", "fact": desc,
                               "headline": product["name"], "benefit": "官方规格与说明书可直接查证",
                               "copy": desc})

    scenes = [{"scene": s, "angle": f"以「{s}」为真实使用场景，突出{cat['lead']}"}
              for s in cat["scenes"]]

    shots = []
    for no, (sec, visual, overlay, voice, ref) in enumerate(cat["shots"], start=1):
        shots.append({
            "no": no,
            "timecode": sec,
            "visual": _fill(visual, v),
            "overlay_text": _fill(overlay, v),
            "voiceover": _fill(voice, v),
            "asset_ref": ref,
        })

    keywords = [
        product["sku"], product["name"], product.get("brand", "P4"), cat_cn,
        *cat["scenes"], *[p["label"] for p in selling_points], "官方驱动", "说明书",
    ]
    return {
        "tagline": tagline,
        "lead": cat["lead"],
        "short_copy": short_copy,
        "long_copy": long_copy,
        "selling_points": selling_points,
        "scenes": scenes,
        "video_script": {
            "title": f"{product['name']} · 30 秒产品短片",
            "duration_seconds": 30,
            "aspect_ratio": "16:9",
            "style": "浅色专业电竞外设 / 干净棚拍 / 快切；中性灰底 + 单一红色强调",
            "brand_tone": BRAND["tone"],
            "shots": shots,
            "bgm_hint": cat["bgm"],
            "cta": f"前往 P4 资源下载中心搜索 {product['sku']}，获取驱动与完整说明书",
            "compliance_note": "字幕与口播中的数字参数均取自商品库真实规格，不得自行追加未标注性能；"
                               "个别规格缺失处使用不含数字的描述性措辞，仅为语句完整，不作为性能承诺",
        },
        "keywords": list(dict.fromkeys(keywords)),
    }


def category_audience(cat: str) -> str:
    return {
        "keyboard": "键盘玩家与长时间打字用户", "mouse": "竞技玩家与中大手用户",
        "headset": "FPS 玩家与远程会议用户", "webcam": "会议、直播与录课用户",
        "dock": "多屏办公与内容创作者", "gamepad": "多平台游戏玩家",
    }.get(cat, "外设用户")


def selling_brief(flat: dict[str, str], cat: dict) -> str:
    """长文案里的一句参数摘要：只取该品类前 3 个真实存在的卖点。"""
    picked = []
    for key, label, _ in cat["highlights"]:
        val = _pick(flat, _ALIASES.get(key, [key]))
        if val.strip() not in _EMPTY_MARKS:
            picked.append(f"{label} {val}")
        if len(picked) == 3:
            break
    return "、".join(picked)
