"""
种子数据。

- 26 个外设主力 SKU（键盘 / 鼠标 / 耳机 / 摄像头 / 扩展坞 / 手柄），带「电竞级」分组规格。
- 规格按域分组（核心参数 / 人体工学 / 连接 / 电竞特性 / 兼容 / 设计），售前问答直接命中字段。
- 关键拆分维度：
  · 鼠标：大手 / 中手 / 小手 × 无线 / 有线
  · 键盘：配列长度（60% / 75% / TKL / 全尺寸）× 键帽材质（PBT / ABS）× 机械 / 磁轴
  · 耳机：是否 ANC（主动降噪）× 无线 / 有线
  · 全品类均覆盖 有线 / 无线
- 每个产品配套驱动（Windows / macOS / Linux），驱动下载是外设场景核心能力。
- 演示买家 buyer_demo 的近 90 天订单（含不同状态，用于演示查单 / 改地址 / 取消 / 售后）。
- 结构化政策（退换、保修、时效），策略引擎直接命中。

运行：python -m app.db.seed
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid

from sqlalchemy import select, func

from app.db.models import (
    Driver,
    Manual,
    Order,
    OrderItem,
    OrderStatus,
    PolicyRule,
    Product,
    Resource,
)
from app.db.session import AsyncSessionLocal, Base, engine, init_db
from app.services import driver_service, resource_service as rsvc


# ---------------------------------------------------------------------------
# 兼容系统模板（减少重复，保持口径一致）
# ---------------------------------------------------------------------------
COMPAT_PC = ["Windows 10+", "macOS 11+", "Linux"]
COMPAT_CAM = ["Windows 10+", "macOS 11+", "Linux (UVC)"]
COMPAT_DOCK = ["Windows 10+", "macOS 11+", "Linux", "iPadOS", "Android"]
COMPAT_AUDIO = ["Windows", "macOS", "Android", "iOS", "PS5", "Switch"]
COMPAT_PAD = ["Windows 10+", "Android", "Switch", "Linux"]


# ---------------------------------------------------------------------------
# 分组规格构造器：每个品类一套，保证参数口径专业且一致
# ---------------------------------------------------------------------------
def kb_specs(*, layout, keys, switch_kind, switch_detail, hotswap, actuation_mm,
             actuation_g, connect, top_material, keycap_material, keycap_craft,
             structure, polling_hz, rgb, software, compat, size_mm, weight_g,
             rapid_trigger=False) -> dict:
    g = {
        "核心参数": {
            "配列": layout, "键数": keys, "轴体类型": switch_kind, "轴座": hotswap,
            "触发键程": f"{actuation_mm} mm", "触发压力": f"{actuation_g} g", "连接": connect,
        },
        "做工与材质": {
            "上盖材质": top_material, "键帽材质": keycap_material,
            "字符工艺": keycap_craft, "结构": structure,
        },
        "性能与灯效": {
            "回报率": f"{polling_hz} Hz", "全键无冲": "支持",
            "板载内存": "支持", "RGB 背光": rgb, "可编程": "全键自定义",
        },
        "软件": {"驱动软件": software, "板载配置": "支持（免驱切换）"},
        "兼容系统": compat,
        "设计": {"尺寸": size_mm, "重量": f"{weight_g} g"},
    }
    if rapid_trigger:
        g["性能与灯效"]["RT 快速触发"] = "支持（0.1–4.0 mm 可调，模拟量触发）"
    return g


def mouse_specs(*, sensor, dpi, ips, accel, switch_type, buttons, liftoff_mm,
                click_ms, hand_type, grip, weight_g, size_mm, surface, switch_life,
                connect, polling_hz, battery_h, charge, latency_ms, compat) -> dict:
    return {
        "核心参数": {
            "传感器": sensor, "最高 DPI": dpi, "追踪速度": f"{ips} IPS",
            "最大加速度": f"{accel} G", "微动": switch_type, "按键数": buttons,
            "分辨率档位": "板载 5 档 + 软体自定义",
        },
        "人体工学": {
            "适用手型": hand_type, "推荐握姿": grip, "重量": f"{weight_g} g",
            "尺寸": size_mm, "表面处理": surface, "按键寿命": f"{switch_life} 万次",
        },
        "连接": {
            "连接方式": connect, "回报率": f"{polling_hz} Hz", "续航": f"{battery_h} h",
            "充电": charge, "无线延迟": f"{latency_ms} ms",
        },
        "电竞特性": {
            "静默高度 LOD": f"{liftoff_mm} mm", "点击延迟": f"{click_ms} ms",
            "板载内存": "支持", "脚贴": "100% PTFE", "表面校准": "支持",
        },
        "兼容系统": compat,
    }


def headset_specs(*, driver_mm, driver_type, freq, impedance, sensitivity, anc,
                  anc_type, transparency, enc, connect, protocol, battery_h,
                  latency_ms, multipoint, mic_pattern, mic_detach, mic_rate,
                  compat, weight_g, earpad, headband, size_mm) -> dict:
    return {
        "核心参数": {
            "单元直径": f"{driver_mm} mm", "单元类型": driver_type,
            "频响范围": freq, "阻抗": f"{impedance} Ω", "灵敏度": f"{sensitivity} dB",
        },
        "降噪": {
            "主动降噪 ANC": anc, "ANC 类型": anc_type,
            "通透模式": transparency, "通话降噪 ENC": enc,
        },
        "连接": {
            "连接方式": connect, "无线协议": protocol, "续航": f"{battery_h} h",
            "游戏延迟": f"{latency_ms} ms", "多点连接": multipoint,
        },
        "麦克风": {
            "指向性": mic_pattern, "可拆卸": mic_detach,
            "采样率": mic_rate, "AI 防风噪": "支持",
        },
        "兼容平台": compat,
        "设计": {"重量": f"{weight_g} g", "耳罩": earpad, "头梁": headband, "尺寸": size_mm},
    }


def webcam_specs(*, resolution, fps, sensor, aperture, fov, focus, zoom, iface,
                 bandwidth, cable_m, compat, privacy, light, framing, mic_array,
                 weight_g, size_mm, stand) -> dict:
    return {
        "核心参数": {
            "分辨率": resolution, "帧率": fps, "传感器": sensor, "光圈": aperture,
            "视场角 FOV": f"{fov}°", "对焦": focus, "数码变焦": zoom,
        },
        "连接": {
            "接口": iface, "带宽": bandwidth, "线缆长度": f"{cable_m} m", "兼容": compat,
        },
        "特性": {
            "隐私遮罩": privacy, "智能补光": light, "自动取景构图": framing,
            "麦克风": mic_array, "AI 降噪": "支持",
        },
        "设计": {"重量": f"{weight_g} g", "尺寸": size_mm, "支架": stand},
    }


def dock_specs(*, ports_total, video, max_res, pd_w, ethernet, card_reader,
               data_ports, host_iface, compat, weight_g, size_mm, cooling,
               material) -> dict:
    return {
        "核心参数": {
            "接口总数": f"{ports_total} 合 1", "视频输出": video, "最大分辨率": max_res,
            "PD 供电": f"{pd_w} W", "网口": ethernet, "读卡器": card_reader,
            "数据接口": data_ports,
        },
        "连接": {"主机接口": host_iface, "兼容系统": compat},
        "设计": {"重量": f"{weight_g} g", "尺寸": size_mm, "散热": cooling, "外壳": material},
    }


def gamepad_specs(*, stick, trigger, motor, back_buttons, face, connect, polling_hz,
                  battery_h, latency_ms, wired_iface, compat, weight_g, size_mm,
                  surface) -> dict:
    return {
        "核心参数": {
            "摇杆类型": stick, "扳机": trigger, "马达": motor,
            "背键数": back_buttons, "面键": face,
        },
        "连接": {
            "连接方式": connect, "回报率": f"{polling_hz} Hz", "续航": f"{battery_h} h",
            "延迟": f"{latency_ms} ms", "有线接口": wired_iface,
        },
        "兼容平台": compat,
        "设计": {"重量": f"{weight_g} g", "尺寸": size_mm, "表面": surface},
    }


# ---------------------------------------------------------------------------
# 商品目录（26 个 SKU）
# ---------------------------------------------------------------------------
PRODUCTS = [
    # ===================== 键盘 =====================
    {
        "sku": "KB-PRO87", "name": "P4 机械键盘 Pro 87", "brand": "P4Gear", "category": "keyboard",
        "price": 499.0, "stock": 120, "featured": True,
        "description": "87 键 75% 配列 Gasket 机械键盘，热插拔轴座，铝合金上盖 + PBT 双色键帽，有线/2.4G/蓝牙三模。",
        "specs": kb_specs(
            layout="75% / 87 键", keys=87, switch_kind="机械轴（线性/段落可选）",
            switch_detail="", hotswap="热插拔 5pin", actuation_mm=2.0, actuation_g=45,
            connect="三模（USB-C / 2.4G / 蓝牙 5.1）", top_material="铝合金上盖",
            keycap_material="PBT 双色注塑", keycap_craft="双色注塑", structure="Gasket 结构",
            polling_hz=1000, rgb="RGB（1680 万色，南向）", software="P4 Master / VIA",
            compat=COMPAT_PC, size_mm="358×130×32", weight_g=780,
        ),
    },
    {
        "sku": "KB-TKL", "name": "P4 客制化键盘 TKL", "brand": "P4Gear", "category": "keyboard",
        "price": 699.0, "stock": 40, "featured": False,
        "description": "84 键 TKL 铝坨坨客制化键盘，CNC 一体成型，PBT 热升华键帽，支持 QMK/VIA 全键可编程。",
        "specs": kb_specs(
            layout="80% / TKL 84 键", keys=84, switch_kind="机械轴（客制化）",
            switch_detail="", hotswap="热插拔 5pin", actuation_mm=2.0, actuation_g=50,
            connect="三模（USB-C / 2.4G / 蓝牙 5.1）", top_material="CNC 铝合金（阳极）",
            keycap_material="PBT 热升华", keycap_craft="热升华", structure="定位板 + 硅胶垫",
            polling_hz=1000, rgb="RGB（北向）", software="QMK / VIA",
            compat=COMPAT_PC, size_mm="360×135×33", weight_g=1100,
        ),
    },
    {
        "sku": "KB-60HE", "name": "P4 磁轴键盘 60HE", "brand": "P4Gear", "category": "keyboard",
        "price": 599.0, "stock": 70, "featured": True,
        "description": "61 键 60% 磁轴（霍尔效应）电竞键盘，模拟量触发 + RT 快速触发，0.1mm 步进调行程，竞技专用。",
        "specs": kb_specs(
            layout="60% / 61 键", keys=61, switch_kind="磁轴（Hall-effect）",
            switch_detail="电磁感应，无物理触点", hotswap="磁轴专用（不可换机械轴）",
            actuation_mm=0.2, actuation_g=30, connect="有线 USB-C", top_material="铝合金上盖",
            keycap_material="P4 贴牌 PBT", keycap_craft="双色注塑", structure="Gasket 结构",
            polling_hz=8000, rgb="RGB（南向）", software="P4 Master（RT 调参）",
            compat=COMPAT_PC, size_mm="295×105×28", weight_g=620, rapid_trigger=True,
        ),
    },
    {
        "sku": "KB-FULL", "name": "P4 机械键盘 Full 104", "brand": "P4Gear", "category": "keyboard",
        "price": 329.0, "stock": 150, "featured": False,
        "description": "104 键全尺寸机械键盘，ABS 双色注塑键帽，船壳结构，有线直连，办公/游戏两用。",
        "specs": kb_specs(
            layout="100% / 104 键", keys=104, switch_kind="机械轴（红/茶/青）",
            switch_detail="", hotswap="固定轴座（出厂润）", actuation_mm=2.0, actuation_g=45,
            connect="有线 USB-C", top_material="ABS 上盖", keycap_material="ABS 双色注塑",
            keycap_craft="双色注塑", structure="船壳结构", polling_hz=1000,
            rgb="单色白光", software="即插即用（免驱）",
            compat=COMPAT_PC, size_mm="440×135×35", weight_g=980,
        ),
    },
    {
        "sku": "KB-75PBT", "name": "P4 机械键盘 75 PBT", "brand": "P4Gear", "category": "keyboard",
        "price": 459.0, "stock": 90, "featured": False,
        "description": "82 键 75% 配列，强调 PBT 材质手感：原厂高度 PBT 双色键帽 + 铝合金上盖，热插拔三模。",
        "specs": kb_specs(
            layout="75% / 82 键", keys=82, switch_kind="机械轴（线性/段落可选）",
            switch_detail="", hotswap="热插拔 5pin", actuation_mm=2.0, actuation_g=45,
            connect="三模（USB-C / 2.4G / 蓝牙 5.1）", top_material="铝合金上盖",
            keycap_material="原厂高度 PBT 双色", keycap_craft="双色注塑", structure="Gasket 结构",
            polling_hz=1000, rgb="RGB（南向）", software="P4 Master / VIA",
            compat=COMPAT_PC, size_mm="332×125×30", weight_g=760,
        ),
    },
    {
        "sku": "KB-TKLHE", "name": "P4 磁轴键盘 TKL HE", "brand": "P4Gear", "category": "keyboard",
        "price": 899.0, "stock": 35, "featured": False,
        "description": "87 键 TKL 磁轴电竞旗舰，铝合金 CNC 机身，RT 快速触发 + 动态键程，职业选手同款。",
        "specs": kb_specs(
            layout="80% / TKL 87 键", keys=87, switch_kind="磁轴（Hall-effect）",
            switch_detail="电磁感应，无物理触点", hotswap="磁轴专用（不可换机械轴）",
            actuation_mm=0.1, actuation_g=28, connect="三模（USB-C / 2.4G / 蓝牙 5.1）",
            top_material="CNC 铝合金（阳极）", keycap_material="PBT 双色注塑",
            keycap_craft="双色注塑", structure="定位板 + 硅胶垫", polling_hz=8000,
            rgb="RGB（北向）", software="P4 Master（RT 调参）",
            compat=COMPAT_PC, size_mm="362×138×34", weight_g=1180, rapid_trigger=True,
        ),
    },

    # ===================== 鼠标 =====================
    {
        "sku": "MS-ULTRA", "name": "P4 无线鼠标 Ultra（中手）", "brand": "P4Gear", "category": "mouse",
        "price": 299.0, "stock": 200, "featured": True,
        "description": "中手型轻量化无线鼠标，58g 机身，原相 PAW3950 旗舰引擎，26000 DPI / 4000Hz 回报率。",
        "specs": mouse_specs(
            sensor="原相 PAW3950", dpi="26000", ips=650, accel=50, switch_type="光学微动",
            buttons=6, liftoff_mm=1.0, click_ms=1.0, hand_type="中手（17–19cm）",
            grip="趴握 / 抓握", weight_g=58, size_mm="122×63×38", surface="磨砂 UV 涂层",
            switch_life=80, connect="2.4G 无线 + 蓝牙 5.0", polling_hz=4000, battery_h=70,
            charge="USB-C（1.5h 充满）", latency_ms=1.2, compat=COMPAT_PC,
        ),
    },
    {
        "sku": "MS-MID-D", "name": "P4 有线鼠标 Mid-D（中手）", "brand": "P4Gear", "category": "mouse",
        "price": 249.0, "stock": 180, "featured": False,
        "description": "中手型有线电竞鼠标，59g 轻量，PAW3950，8000Hz 有线回报率，职业赛训同款。",
        "specs": mouse_specs(
            sensor="原相 PAW3950", dpi="26000", ips=650, accel=50, switch_type="光学微动",
            buttons=6, liftoff_mm=1.0, click_ms=0.6, hand_type="中手（17–19cm）",
            grip="抓握 / 指握", weight_g=59, size_mm="121×63×38", surface="磨砂 UV 涂层",
            switch_life=80, connect="有线 USB-C（伞绳）", polling_hz=8000, battery_h=0,
            charge="—（有线直供）", latency_ms=0.4, compat=COMPAT_PC,
        ),
    },
    {
        "sku": "MS-LARGE-W", "name": "P4 无线鼠标 Large-W（大手）", "brand": "P4Gear", "category": "mouse",
        "price": 319.0, "stock": 140, "featured": True,
        "description": "大手型人体工学无线鼠标，89g 配重优化，PAW3950，适合 19cm 以上手型趴握。",
        "specs": mouse_specs(
            sensor="原相 PAW3950", dpi="26000", ips=650, accel=50, switch_type="光学微动",
            buttons=7, liftoff_mm=1.0, click_ms=1.0, hand_type="大手（19cm+）",
            grip="趴握（右手人体工学）", weight_g=89, size_mm="131×68×42", surface="类肤涂层",
            switch_life=80, connect="2.4G 无线 + 蓝牙 5.0", polling_hz=4000, battery_h=80,
            charge="USB-C（2h 充满）", latency_ms=1.4, compat=COMPAT_PC,
        ),
    },
    {
        "sku": "MS-LARGE-D", "name": "P4 有线鼠标 Large-D（大手）", "brand": "P4Gear", "category": "mouse",
        "price": 269.0, "stock": 130, "featured": False,
        "description": "大手型有线电竞鼠标，95g，PAW3950，8000Hz 回报率，长对称机身。",
        "specs": mouse_specs(
            sensor="原相 PAW3950", dpi="26000", ips=650, accel=50, switch_type="光学微动",
            buttons=7, liftoff_mm=1.0, click_ms=0.6, hand_type="大手（19cm+）",
            grip="趴握 / 抓握", weight_g=95, size_mm="130×67×42", surface="磨砂 UV 涂层",
            switch_life=80, connect="有线 USB-C（伞绳）", polling_hz=8000, battery_h=0,
            charge="—（有线直供）", latency_ms=0.4, compat=COMPAT_PC,
        ),
    },
    {
        "sku": "MS-SMALL-W", "name": "P4 无线鼠标 Small-W（小手）", "brand": "P4Gear", "category": "mouse",
        "price": 279.0, "stock": 160, "featured": True,
        "description": "小手型超轻无线鼠标，49g 极致轻量，原相 PAW3395，适合 17cm 以下手型指握。",
        "specs": mouse_specs(
            sensor="原相 PAW3395", dpi="26000", ips=650, accel=50, switch_type="光学微动",
            buttons=5, liftoff_mm=1.0, click_ms=1.0, hand_type="小手（≤17cm）",
            grip="指握 / 抓握", weight_g=49, size_mm="116×58×36", surface="细磨砂涂层",
            switch_life=80, connect="2.4G 无线 + 蓝牙 5.0", polling_hz=4000, battery_h=75,
            charge="USB-C（1.2h 充满）", latency_ms=1.2, compat=COMPAT_PC,
        ),
    },
    {
        "sku": "MS-SMALL-D", "name": "P4 有线鼠标 Small-D（小手）", "brand": "P4Gear", "category": "mouse",
        "price": 219.0, "stock": 170, "featured": False,
        "description": "小手型有线超轻鼠标，52g，PAW3395，8000Hz 回报率，紧凑对称机身。",
        "specs": mouse_specs(
            sensor="原相 PAW3395", dpi="26000", ips=650, accel=50, switch_type="光学微动",
            buttons=5, liftoff_mm=1.0, click_ms=0.6, hand_type="小手（≤17cm）",
            grip="指握 / 抓握", weight_g=52, size_mm="115×58×36", surface="细磨砂涂层",
            switch_life=80, connect="有线 USB-C（伞绳）", polling_hz=8000, battery_h=0,
            charge="—（有线直供）", latency_ms=0.4, compat=COMPAT_PC,
        ),
    },
    {
        "sku": "MS-LITE", "name": "P4 无线鼠标 Lite（入门）", "brand": "P4Gear", "category": "mouse",
        "price": 159.0, "stock": 300, "featured": False,
        "description": "入门中手型静音无线鼠标，85g，原相 PAW3311，办公轻游戏两用，静音点击。",
        "specs": mouse_specs(
            sensor="原相 PAW3311", dpi="16000", ips=400, accel=40, switch_type="静音机械微动",
            buttons=3, liftoff_mm=2.0, click_ms=2.0, hand_type="中手（通用）",
            grip="趴握", weight_g=85, size_mm="115×60×38", surface="抛光涂层",
            switch_life=50, connect="2.4G 无线", polling_hz=1000, battery_h=120,
            charge="USB-C（2.5h 充满）", latency_ms=3.0, compat=COMPAT_PC,
        ),
    },

    # ===================== 耳机 =====================
    {
        "sku": "HS-ANC7", "name": "P4 降噪耳机 ANC7（无线）", "brand": "P4Audio", "category": "headset",
        "price": 899.0, "stock": 80, "featured": True,
        "description": "双反馈混合 ANC 头戴耳机，40mm 动圈，2.4G 低延迟游戏模式，续航 35h。",
        "specs": headset_specs(
            driver_mm=40, driver_type="动圈", freq="20Hz–40kHz", impedance=32, sensitivity=105,
            anc="支持", anc_type="混合式双反馈（前馈+反馈）", transparency="支持",
            enc="AI 双麦 ENC", connect="2.4G + 蓝牙 5.3 + 3.5mm", protocol="P4 低延迟 2.4G",
            battery_h=35, latency_ms=60, multipoint="支持（双设备）", mic_pattern="全指向",
            mic_detach="可拆卸", mic_rate="48kHz / 16bit", compat=COMPAT_AUDIO,
            weight_g=290, earpad="记忆海绵 + 蛋白皮", headband="金属头梁", size_mm="190×170×80",
        ),
    },
    {
        "sku": "HS-ANC-PRO", "name": "P4 降噪耳机 ANC Pro（无线旗舰）", "brand": "P4Audio", "category": "headset",
        "price": 1299.0, "stock": 45, "featured": True,
        "description": "50mm 镀铍单元旗舰降噪耳机，2.4G 20ms 超低延迟，混合 ANC + LDAC 高码率，续航 60h。",
        "specs": headset_specs(
            driver_mm=50, driver_type="镀铍动圈", freq="10Hz–40kHz", impedance=32, sensitivity=108,
            anc="支持", anc_type="自适应混合 ANC（-48dB）", transparency="支持（环境感知）",
            enc="三麦 AI ENC", connect="2.4G + 蓝牙 5.3 + 3.5mm", protocol="P4 低延迟 2.4G / LDAC",
            battery_h=60, latency_ms=20, multipoint="支持（三设备）", mic_pattern="心形指向",
            mic_detach="可拆卸", mic_rate="48kHz / 24bit", compat=COMPAT_AUDIO,
            weight_g=320, earpad="慢回弹记忆棉 + 蛋白皮", headband="金属头梁", size_mm="200×180×85",
        ),
    },
    {
        "sku": "HS-ANC-D", "name": "P4 降噪耳机 ANC-D（有线）", "brand": "P4Audio", "category": "headset",
        "price": 599.0, "stock": 70, "featured": False,
        "description": "有线 ANC 游戏耳机，40mm 单元，USB-C / 3.5mm 双模，AI ENC 通话降噪，零续航焦虑。",
        "specs": headset_specs(
            driver_mm=40, driver_type="动圈", freq="20Hz–20kHz", impedance=32, sensitivity=102,
            anc="支持", anc_type="前馈 ANC（有线生效）", transparency="不支持",
            enc="AI 单麦 ENC", connect="USB-C + 3.5mm", protocol="有线直连",
            battery_h=0, latency_ms=0, multipoint="不支持", mic_pattern="全指向",
            mic_detach="不可拆卸", mic_rate="16kHz / 16bit", compat=COMPAT_AUDIO,
            weight_g=280, earpad="记忆海绵 + 蛋白皮", headband="金属头梁", size_mm="188×165×78",
        ),
    },
    {
        "sku": "HS-OPEN", "name": "P4 开放式耳机 Open（无线）", "brand": "P4Audio", "category": "headset",
        "price": 649.0, "stock": 50, "featured": False,
        "description": "开放式头戴耳机，定向声场不闷耳，40mm 单元，长续航，运动 / 通勤友好。",
        "specs": headset_specs(
            driver_mm=40, driver_type="动圈", freq="20Hz–20kHz", impedance=32, sensitivity=104,
            anc="不支持", anc_type="—", transparency="开放式（天然通透）",
            enc="AI 双麦 ENC", connect="蓝牙 5.3 + 3.5mm", protocol="AAC / SBC",
            battery_h=50, latency_ms=70, multipoint="支持", mic_pattern="全指向",
            mic_detach="不可拆卸", mic_rate="48kHz / 16bit", compat=COMPAT_AUDIO,
            weight_g=260, earpad="网布透气耳罩", headband="轻量头梁", size_mm="180×160×75",
        ),
    },
    {
        "sku": "HS-IEM", "name": "P4 入耳监听耳机 IEM（有线）", "brand": "P4Audio", "category": "headset",
        "price": 399.0, "stock": 110, "featured": True,
        "description": "双单元入耳式电竞监听耳塞，10mm 动圈 + 动铁，3.5mm 无感延迟，FPS 脚步定位专精。",
        "specs": headset_specs(
            driver_mm=10, driver_type="双单元（动圈+动铁）", freq="10Hz–30kHz", impedance=16,
            sensitivity=110, anc="不支持", anc_type="—", transparency="—（入耳被动隔噪）",
            enc="—（无麦克风线）", connect="3.5mm 有线", protocol="有线直连",
            battery_h=0, latency_ms=0, multipoint="不支持", mic_pattern="—",
            mic_detach="—", mic_rate="—", compat=COMPAT_AUDIO,
            weight_g=25, earpad="多尺寸硅胶/记忆棉套", headband="绕耳挂", size_mm="25×20×20",
        ),
    },

    # ===================== 摄像头 =====================
    {
        "sku": "CAM-4K", "name": "P4 4K 直播摄像头", "brand": "P4Vision", "category": "webcam",
        "price": 399.0, "stock": 60, "featured": True,
        "description": "4K 30fps / 1080P 60fps 网络摄像头，自动对焦，自带物理隐私遮罩。",
        "specs": webcam_specs(
            resolution="3840×2160", fps="4K@30 / 1080P@60", sensor="1/2.5\" CMOS",
            aperture="f/2.0", fov=78, focus="自动对焦（PDAF）", zoom="4× 数码",
            iface="USB-A 3.0", bandwidth="5Gbps", cable_m=1.5, compat=COMPAT_CAM,
            privacy="物理滑盖遮罩", light="智能补光（两档）", framing="支持自动取景",
            mic_array="双阵列降噪麦", weight_g=150, size_mm="90×50×50", stand="阻尼折叠支架",
        ),
    },
    {
        "sku": "CAM-1080", "name": "P4 高清摄像头 1080P", "brand": "P4Vision", "category": "webcam",
        "price": 169.0, "stock": 140, "featured": False,
        "description": "1080P 60fps 网络摄像头，自动补光，即插即用，性价比之选。",
        "specs": webcam_specs(
            resolution="1920×1080", fps="1080P@60", sensor="1/3\" CMOS",
            aperture="f/2.2", fov=70, focus="自动对焦", zoom="2× 数码",
            iface="USB-A 2.0", bandwidth="480Mbps", cable_m=1.5, compat=COMPAT_CAM,
            privacy="物理滑盖遮罩", light="自动补光", framing="不支持",
            mic_array="单麦降噪", weight_g=120, size_mm="80×45×45", stand="夹式支架",
        ),
    },
    {
        "sku": "CAM-1440", "name": "P4 2K 主播摄像头 Facecam", "brand": "P4Vision", "category": "webcam",
        "price": 299.0, "stock": 75, "featured": True,
        "description": "2K（2560×1440）60fps 主播 Facecam，大底传感器 + 无畸变镜头，自动构图。",
        "specs": webcam_specs(
            resolution="2560×1440", fps="2K@60 / 1080P@120", sensor="1/2\" 背照式 CMOS",
            aperture="f/1.8", fov=82, focus="定焦（不可调）", zoom="无",
            iface="USB-C 3.0", bandwidth="5Gbps", cable_m=2.0, compat=COMPAT_CAM,
            privacy="磁吸遮罩", light="无（外置补光推荐）", framing="AI 自动构图 + 跟踪",
            mic_array="无（外接麦推荐）", weight_g=130, size_mm="85×48×48", stand="1/4 螺孔 + 桌面夹",
        ),
    },

    # ===================== 扩展坞 =====================
    {
        "sku": "DOCK-11IN1", "name": "P4 扩展坞 11 合 1", "brand": "P4Connect", "category": "dock",
        "price": 599.0, "stock": 90, "featured": True,
        "description": "11 合 1 Type-C 扩展坞，双 HDMI 4K、PD 100W 供电、2.5G 网口，桌面生产力利器。",
        "specs": dock_specs(
            ports_total=11, video="2× HDMI 2.0（双 4K@60）", max_res="双 4K@60Hz",
            pd_w=100, ethernet="2.5G RJ45", card_reader="SD 3.0 / microSD",
            data_ports="3× USB-A 3.2 + 1× USB-C 10Gbps", host_iface="USB-C（DP Alt + PD）",
            compat=COMPAT_DOCK, weight_g=220, size_mm="140×70×18", cooling="铝合金被动散热",
            material="阳极铝合金",
        ),
    },
    {
        "sku": "DOCK-MINI", "name": "P4 扩展坞 Mini 7 合 1", "brand": "P4Connect", "category": "dock",
        "price": 329.0, "stock": 110, "featured": False,
        "description": "便携 7 合 1 扩展坞，PD 65W，HDMI 4K，适合笔记本党随身带。",
        "specs": dock_specs(
            ports_total=7, video="1× HDMI 2.0（4K@30）", max_res="单 4K@30Hz",
            pd_w=65, ethernet="无", card_reader="SD / microSD",
            data_ports="2× USB-A 3.0", host_iface="USB-C（DP Alt + PD）",
            compat=COMPAT_DOCK, weight_g=80, size_mm="110×45×15", cooling="被动散热",
            material="铝合金",
        ),
    },
    {
        "sku": "DOCK-TB4", "name": "P4 雷电扩展坞 TB4", "brand": "P4Connect", "category": "dock",
        "price": 899.0, "stock": 40, "featured": True,
        "description": "Thunderbolt 4 40Gbps 旗舰扩展坞，双 4K@60 / 单 8K，PD 96W，2.5G 网口。",
        "specs": dock_specs(
            ports_total=12, video="2× TB4（DP Alt，双 4K@60 / 单 8K@30）", max_res="双 4K@60Hz",
            pd_w=96, ethernet="2.5G RJ45", card_reader="SD 4.0 / microSD",
            data_ports="3× USB-A 10Gbps + 1× TB4 下行", host_iface="Thunderbolt 4 / USB4",
            compat=COMPAT_DOCK, weight_g=260, size_mm="160×80×22", cooling="铝合金被动散热",
            material="CNC 铝合金",
        ),
    },

    # ===================== 手柄 =====================
    {
        "sku": "GP-XBOX", "name": "P4 无线手柄 X", "brand": "P4Gear", "category": "gamepad",
        "price": 349.0, "stock": 150, "featured": True,
        "description": "多平台无线手柄，霍尔摇杆 + 霍尔线性扳机，4 背键，PC/安卓/Switch 通吃。",
        "specs": gamepad_specs(
            stick="霍尔效应摇杆（无漂移）", trigger="霍尔线性扳机", motor="非对称双马达",
            back_buttons=4, face="导电胶面键", connect="2.4G + 蓝牙 5.2 + 有线",
            polling_hz=1000, battery_h=25, latency_ms=4, wired_iface="USB-C",
            compat=COMPAT_PAD, weight_g=245, size_mm="155×105×62", surface="磨砂涂层",
        ),
    },
    {
        "sku": "GP-WIRED", "name": "P4 有线手柄 W", "brand": "P4Gear", "category": "gamepad",
        "price": 199.0, "stock": 180, "featured": False,
        "description": "性价比有线手柄，碳膜摇杆，3.5mm 耳麦接口，2m 编织线。",
        "specs": gamepad_specs(
            stick="碳膜摇杆", trigger="普通线性扳机", motor="非对称双马达",
            back_buttons=0, face="导电胶面键", connect="有线 USB-A",
            polling_hz=500, battery_h=0, latency_ms=1, wired_iface="USB-A（2m 编织线）",
            compat=COMPAT_PAD, weight_g=230, size_mm="150×100×60", surface="抛光涂层",
        ),
    },
]

# 驱动：每个产品 3 个 OS 版本
OS_LIST = [
    {"os": "Windows", "arch": "x64", "base": 38.0},
    {"os": "macOS", "arch": "universal", "base": 42.0},
    {"os": "Linux", "arch": "x64", "base": 30.0},
]

POLICIES = [
    # 退换：按品类给天数（七天无理由为基础，部分品类更长）
    {"kind": "return", "category": "*", "payload": {"window_days": 7, "condition": "商品完好未激活",
                                                     "no_reason": True, "free_return": False}},
    {"kind": "return", "category": "keyboard", "payload": {"window_days": 7, "condition": "未拆轴/未激活",
                                                           "no_reason": True, "free_return": False}},
    {"kind": "return", "category": "mouse", "payload": {"window_days": 7, "condition": "外观完好",
                                                        "no_reason": True, "free_return": True}},
    # 保修：按品类给月数
    {"kind": "warranty", "category": "*", "payload": {"months": 12, "proof": "订单号即可",
                                                      "exception": "人为进水/摔损不在保"}},
    {"kind": "warranty", "category": "keyboard", "payload": {"months": 24, "proof": "订单号即可",
                                                             "exception": "热插拔轴人为损坏不在保"}},
    {"kind": "warranty", "category": "mouse", "payload": {"months": 18, "proof": "订单号即可",
                                                          "exception": "微动人为损坏不在保"}},
    {"kind": "warranty", "category": "headset", "payload": {"months": 24, "proof": "订单号即可",
                                                            "exception": "耳罩人为破损不在保"}},
    {"kind": "warranty", "category": "dock", "payload": {"months": 18, "proof": "订单号即可",
                                                         "exception": "Type-C 物理损伤不在保"}},
    # 时效：地区 + 承运商承诺天数（催单/安抚话术依据）
    {"kind": "shipping", "category": "*", "payload": {"regions": {"江浙沪": 1, "京津冀": 2, "珠三角": 1,
                                                                  "其他": 3}, "carrier": "顺丰", "cutoff_hour": 16}},
]

BUYER = "buyer_demo"
PHONE = "13800001234"


def _phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        cnt = await db.scalar(select(func.count()).select_from(Product))
        if cnt and cnt > 0:
            print(f"已存在 {cnt} 个商品，跳过种子。")
            return

        pid_by_sku = {}
        for p in PRODUCTS:
            prod = Product(
                sku=p["sku"], name=p["name"], brand=p["brand"], category=p["category"],
                price=p["price"], stock=p["stock"], featured=p.get("featured", False),
                description=p["description"], specs=json.dumps(p["specs"], ensure_ascii=False),
                image=f"/static/products/{p['sku']}.svg",
            )
            db.add(prod)
            await db.flush()
            pid_by_sku[p["sku"]] = prod.id

            for osdef in OS_LIST:
                size = round(osdef["base"] + (p["price"] % 10) * 0.1, 1)
                d = Driver(
                    product_id=prod.id, name=f"{p['name']} 驱动",
                    version="2.3.1", os=osdef["os"], arch=osdef["arch"], size_mb=size,
                    release_date="2026-10-01",
                    changelog="修复 Win11 24H2 兼容性问题；新增 macOS Sequoia 支持。",
                    file_url="", sha256="",
                )
                db.add(d)
                await db.flush()
                # 用驱动自身 id 拼接下载地址（下载路由按 driver_id 查找），保证资源页 / 商品详情页均可正确下载
                d.file_url = f"/api/drivers/{d.id}/download?os={osdef['os']}"
                d.sha256 = driver_service.driver_sha256(d)

            # 说明书（中文）+ 资源（快速入门、保修卡）：每个型号一份，资源下载中心可直接检索
            man = Manual(
                product_id=prod.id, title=f"{p['name']} 用户手册", lang="zh",
                version="1.0", pages=24, size_mb=round(2.0 + (p["price"] % 5) * 0.1, 1),
                release_date="2026-09-15", file_url="", sha256="",
            )
            db.add(man)
            await db.flush()
            man.file_url = f"/api/resources/manual/{man.id}/download"
            rsvc.fill_sha256("manual", man, prod)

            for rs in (
                {"kind": "quick_start", "title": f"{p['name']} 快速入门指南", "size": 1.2, "date": "2026-09-10"},
                {"kind": "warranty", "title": f"{p['name']} 保修服务卡", "size": 0.5, "date": "2026-08-01"},
            ):
                res = Resource(
                    product_id=prod.id, title=rs["title"], kind=rs["kind"],
                    size_mb=rs["size"], release_date=rs["date"], file_url="", sha256="",
                )
                db.add(res)
                await db.flush()
                res.file_url = f"/api/resources/resource/{res.id}/download"
                rsvc.fill_sha256("resource", res, prod)

        # 演示订单：不同状态覆盖三域场景
        now = dt.datetime.utcnow()
        orders_spec = [
            # 已支付待发货 -> 可改地址 / 可取消
            {"status": OrderStatus.PAID, "sku": "KB-PRO87", "qty": 1, "days_ago": 1,
             "addr": ("广东省", "深圳市", "南山区", "科技园路 88 号 A 座 1203")},
            # 已发货 -> 改地址应被拦截（引导拒收）
            {"status": OrderStatus.SHIPPED, "sku": "MS-ULTRA", "qty": 2, "days_ago": 4,
             "addr": ("北京市", "北京市", "海淀区", "中关村大街 1 号 鼎好 5F"),
             "carrier": "顺丰", "tracking": "SF1234567890"},
            # 已完成 -> 走售后/保修
            {"status": OrderStatus.DELIVERED, "sku": "HS-ANC7", "qty": 1, "days_ago": 20,
             "addr": ("上海市", "上海市", "浦东新区", "世纪大道 100 号 恒辉 8F")},
            # 待支付 -> 可取消
            {"status": OrderStatus.PENDING_PAYMENT, "sku": "DOCK-11IN1", "qty": 1, "days_ago": 0,
             "addr": ("浙江省", "杭州市", "西湖区", "文三路 478 号 华星 3F")},
        ]
        for idx, o in enumerate(orders_spec):
            prod = next(p for p in PRODUCTS if p["sku"] == o["sku"])
            pid = pid_by_sku[o["sku"]]
            order_no = f"P4{now.year}{(idx+1):04d}{uuid.uuid4().hex[:4].upper()}"
            created = now - dt.timedelta(days=o["days_ago"])
            order = Order(
                order_no=order_no, buyer_id=BUYER, phone_hash=_phone_hash(PHONE),
                status=o["status"], province=o["addr"][0], city=o["addr"][1],
                district=o["addr"][2], detail=o["addr"][3], contact="演示用户",
                total=round(prod["price"] * o["qty"], 2),
                carrier=o.get("carrier", ""), tracking_no=o.get("tracking", ""),
                created_at=created,
                paid_at=created if o["status"] != OrderStatus.PENDING_PAYMENT else None,
            )
            db.add(order)
            await db.flush()
            db.add(OrderItem(
                order_id=order.id, product_id=pid, sku=o["sku"], name=prod["name"],
                qty=o["qty"], unit_price=prod["price"],
                spec_snapshot=json.dumps(prod["specs"], ensure_ascii=False),
            ))

        for pol in POLICIES:
            db.add(PolicyRule(kind=pol["kind"], category=pol["category"],
                              payload=json.dumps(pol["payload"], ensure_ascii=False)))

        await db.commit()
        print(f"种子数据写入完成：{len(PRODUCTS)} 商品 / {len(PRODUCTS)*len(OS_LIST)} 驱动 "
              f"/ {len(PRODUCTS)} 说明书 / {len(PRODUCTS)*2} 资源 "
              f"/ 4 演示订单 / {len(POLICIES)} 政策规则。")


if __name__ == "__main__":
    asyncio.run(seed())
