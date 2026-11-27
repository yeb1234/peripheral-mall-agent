"""
全量演示测试：一次跑完「工具能力 + 安全链路 + 核心话术 + 前端构建」，产出 JSON 报告。

产物：
    docs/reports/test_report.json      机器可读，前端演示页与发布说明引用它
    docs/reports/test_report.md        人读摘要（可直接贴 PR / 发布说明）

用法：
    python scripts/probe_full.py
    python scripts/probe_full.py --skip-web   # 跳过联网搜索用例（外网慢时）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT.parent / "docs" / "reports"
BASE = "http://127.0.0.1:8000"

# (工具, 话术, 期望 intent, 类型, 判定方式, 测试买家)
# 读类用独立买家，避免 20 条业务用例把 buyer_demo 的 20 次/60s 配额打满；
# 依赖订单的用例才用 buyer_demo（它才有那 5 张演示单）。
CASES: list[tuple[str, str, str, str, str, str]] = [
    # ---- 售前 15 ----
    ("search_manual", "KB-60HE 怎么切换连接模式", "presale", "read", "text", "buyer_presale"),
    ("search_driver", "KB-60HE 的驱动在哪里下载", "presale", "read", "text", "buyer_presale"),
    ("search_products", "有没有 60% 配列的键盘", "presale", "read", "text", "buyer_presale"),
    ("query_spec", "KB-60HE 支持 macOS 吗", "presale", "read", "text", "buyer_presale"),
    ("query_stock", "KB-60HE 还有货吗", "presale", "read", "text", "buyer_presale"),
    ("query_price", "KB-60HE 多少钱", "presale", "read", "text", "buyer_presale"),
    ("query_shipping", "发到广东一般几天到", "presale", "read", "text", "buyer_presale"),
    ("web_search", "帮我搜一下 2026 年国产机械键盘的新品", "presale", "read", "text", "buyer_presale"),
    ("compare_benchmarks", "KB-60HE 和竞品的跑分对比", "presale", "read", "text", "buyer_presale"),
    ("get_bench_facts", "跑分库里 KB-60HE 的延迟数据是多少", "presale", "read", "text", "buyer_presale"),
    ("search_bench_products", "跑分库里有哪些 60% 键盘", "presale", "read", "text", "buyer_presale"),
    ("compare_products", "KB-60HE 和 KB-PRO87 有什么区别", "presale", "read", "text", "buyer_presale"),
    ("check_compatibility", "KB-60HE 和 DOCK-TB4 能一起用吗", "presale", "read", "text", "buyer_presale"),
    ("recommend_config", "预算 3000 配一套键盘和耳机", "presale", "read", "text", "buyer_presale"),
    ("query_policy", "保修多久", "presale", "read", "text", "buyer_presale"),
    # ---- 售中 7（3 个写）----
    ("query_order", "查一下我的订单", "insale", "read", "text", "buyer_demo"),
    ("query_logistics", "查一下订单 AA42 的物流", "insale", "read", "text", "buyer_demo"),
    # ---- 售后 6 ----
    ("aftersale_accept", "键盘按键失灵了，申请寄修", "aftersale", "write", "confirm_or_policy", "buyer_demo"),
    ("compensation", "物流太慢了，能补偿吗", "aftersale", "write", "text", "buyer_demo"),
    ("diagnose_issue", "键盘连不上电脑怎么办", "aftersale", "read", "text", "buyer_demo"),
]

# 写操作单独测（会出确认卡，不执行）
WRITE_CASES = [
    ("change_address", "把订单 P420260001FF8B 的收货地址改成：山东省烟台市莱山区科技大道100号3号楼502", "insale", "buyer_demo"),
    ("cancel_order", "取消我的待支付订单", "insale", "buyer_demo"),
]


def sse_chat(msg: str, buyer: str = "buyer_demo", timeout: float = 240.0) -> dict:
    out = {"text": "", "meta": {}, "error": None, "ms": 0}
    t0 = time.time()
    with httpx.Client(timeout=timeout) as c:
        with c.stream("POST", BASE + "/api/chat",
                      json={"message": msg, "buyer_id": buyer, "channel": "web"}) as r:
            if r.status_code != 200:
                out["error"] = f"HTTP{r.status_code}"
                return out
            ev = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:") and ev:
                    raw = line[5:].lstrip()
                    if ev == "token":
                        out["text"] += raw.replace("\\n", "\n")
                    elif ev == "meta":
                        try:
                            out["meta"] = json.loads(raw)
                        except Exception:
                            pass
                    elif ev == "error":
                        try:
                            out["error"] = json.loads(raw)
                        except Exception:
                            out["error"] = raw
    out["ms"] = int((time.time() - t0) * 1000)
    return out


def judge(kind: str, r: dict) -> tuple[bool, str]:
    if r["error"]:
        return False, f"error={r['error']}"
    text = (r["text"] or "").strip()
    if not text:
        return False, "空回复"
    meta = r["meta"] or {}
    if kind == "confirm_or_policy":
        if meta.get("confirmation"):
            return True, "出确认卡"
        if meta.get("handoff") or "7 天" in text or "超过" in text or "保修" in text:
            return True, "政策判定/转人工"
        return False, "既无确认卡也无政策结论"
    if kind == "confirm":
        return (bool(meta.get("confirmation")), "出确认卡" if meta.get("confirmation") else "未出卡")
    return True, f"{len(text)} 字"


def run_cases(skip_web: bool) -> list[dict]:
    rows = []
    rid = str(int(time.time()))[-5:]
    for tool, q, expect, kind, judge_kind, buyer in CASES:
        if skip_web and tool == "web_search":
            rows.append({"tool": tool, "q": q, "skipped": True})
            continue
        # 售前读用例用一次性买家，避免和上一轮测试的限流窗口撞车
        if buyer.startswith("buyer_presale"):
            buyer = f"{buyer}_{rid}"
        r = sse_chat(q, buyer=buyer)
        ok, why = judge(judge_kind, r)
        meta = r["meta"] or {}
        rows.append({
            "tool": tool, "q": q, "expect": expect, "kind": kind,
            "intent": meta.get("intent"), "confirm": bool(meta.get("confirmation")),
            "handoff": bool(meta.get("handoff")), "error_code": meta.get("error_code"),
            "ms": r["ms"], "ok": ok, "why": why,
            "reply": (r["text"] or "").strip().replace("\n", " ")[:220],
        })
        print(f"[{'OK' if ok else 'FAIL'}] {tool:22} {r['ms']:>6}ms  {why}  {q[:28]}")
        time.sleep(1)
    return rows


def run_write_cases() -> list[dict]:
    rows = []
    for tool, q, expect, buyer in WRITE_CASES:
        r = sse_chat(q, buyer=buyer)
        meta = r["meta"] or {}
        ok = bool(meta.get("confirmation")) and not r["error"]
        rows.append({
            "tool": tool, "q": q, "expect": expect, "kind": "write",
            "intent": meta.get("intent"), "confirm": bool(meta.get("confirmation")),
            "handoff": bool(meta.get("handoff")),
            "ms": r["ms"], "ok": ok,
            "preview": (meta.get("confirmation") or {}).get("preview"),
            "reply": (r["text"] or "").strip().replace("\n", " ")[:220],
        })
        print(f"[{'OK' if ok else 'FAIL'}] {tool:22} {r['ms']:>6}ms  confirm={bool(meta.get('confirmation'))}")
        time.sleep(1)
    return rows


def run_security() -> dict:
    script = ROOT / "scripts" / "probe_security.py"
    try:
        p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                           timeout=420, encoding="utf-8", errors="ignore")
        out = p.stdout or ""
        summary = ""
        for line in out.splitlines():
            if line.startswith("合计"):
                summary = line.strip()
        return {"exit": p.returncode, "summary": summary, "fails": [l for l in out.splitlines() if l.startswith("[FAIL]")]}
    except Exception as e:  # noqa: BLE001
        return {"exit": -1, "summary": f"{type(e).__name__}: {e}", "fails": []}


def run_static() -> dict:
    script = ROOT / "scripts" / "security_scan.py"
    try:
        p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                           timeout=120, encoding="utf-8", errors="ignore")
        lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
        fails = [l for l in lines if l.startswith("[FAIL]")]
        warn = [l for l in lines if l.startswith("[WARN]")]
        scanned = next((l for l in lines if l.startswith("scanned files")), "")
        return {"exit": p.returncode, "scanned": scanned, "fails": fails, "warns": warn}
    except Exception as e:  # noqa: BLE001
        return {"exit": -1, "scanned": "", "fails": [f"{type(e).__name__}: {e}"], "warns": []}


def run_frontend_build() -> dict:
    fe = ROOT.parent / "frontend"
    try:
        p = subprocess.run("npm run build", cwd=str(fe), capture_output=True, text=True,
                           timeout=300, shell=True, encoding="utf-8", errors="ignore")
        tail = (p.stdout or "").strip().splitlines()[-4:]
        return {"exit": p.returncode, "tail": tail}
    except Exception as e:  # noqa: BLE001
        return {"exit": -1, "tail": [f"{type(e).__name__}: {e}"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-web", action="store_true")
    args = ap.parse_args()

    try:
        health = httpx.get(BASE + "/api/health", timeout=8).json()
    except Exception as e:  # noqa: BLE001
        print(f"后端未启动：{BASE}  {e}")
        return 2

    started = dt.datetime.now().isoformat(timespec="seconds")
    cases = run_cases(args.skip_web)
    writes = run_write_cases()
    print("\n== 安全动态 ==")
    sec = run_security()
    print(sec["summary"])
    print("== 静态扫描 ==")
    static = run_static()
    print(static["scanned"], "| FAIL", len(static["fails"]), "| WARN", len(static["warns"]))
    print("== 前端构建 ==")
    fe = run_frontend_build()
    print("exit", fe["exit"], fe["tail"])

    total = len(cases) + len(writes)
    skipped = sum(1 for c in cases if c.get("skipped"))
    ok = sum(1 for c in cases if c.get("ok")) + sum(1 for w in writes if w.get("ok"))
    failed = total - ok - skipped
    report = {
        "generated_at": started,
        "health": health,
        "summary": {"cases": total, "passed": ok, "skipped": skipped, "failed": failed},
        "cases": cases,
        "writes": writes,
        "security": sec,
        "static": static,
        "frontend": fe,
    }
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "test_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# 全量测试报告", "",
        f"- 生成时间：{started}",
        f"- 环境：{health.get('llm_provider')} / cache={health.get('cache')} / nlu={health.get('nlu_mode')}",
        f"- 用例：{total} 条，通过 {ok}，跳过 {skipped}，失败 {failed}",
        f"- 安全动态：{sec['summary']}",
        f"- 静态扫描：{static['scanned']}，FAIL {len(static['fails'])}，WARN {len(static['warns'])}",
        f"- 前端构建：exit {fe['exit']}", "",
        "| # | 工具 | 话术 | 意图 | 出卡 | 耗时 | 结果 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, c in enumerate(cases + writes, 1):
        md.append(
            f"| {i} | {c['tool']} | {c['q'][:36]} | {c.get('intent')} | "
            f"{'是' if c.get('confirm') else '否'} | {c.get('ms')}ms | "
            f"{'SKIP' if c.get('skipped') else ('PASS' if c.get('ok') else 'FAIL')} |"
        )
    (DOCS / "test_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"\n报告已写出：{DOCS / 'test_report.json'}")
    print(f"通过 {ok}/{total}（跳过 {skipped}，失败 {failed}）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
