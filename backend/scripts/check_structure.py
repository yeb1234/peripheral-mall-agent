"""Harness 结构自检（按 yeb-agent-harness v1.0 的硬约束逐条查）。

P4 是「单体 FastAPI + app/agents 包」的渐进分层形态，不是 backend/agent 双服务，
所以跨层规则按「包边界」检查：routers/services/core 不得反向依赖 agents；
agents 不得直接操作缓存驱动（只能走 services）。

输出分级：ERROR 必须改，WARN 需要给出理由或排期。
退出码：有 ERROR → 1；--strict 时 WARN 也算失败。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # 项目根
BACKEND = ROOT / "backend"
APP = BACKEND / "app"

errors: list[str] = []
warns: list[str] = []


def _py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def check_layout() -> None:
    required = ["backend", "docs", "docker-compose.yml", "README.md"]
    for item in required:
        if not (ROOT / item).exists():
            errors.append(f"缺少约定目录/文件：{item}")
    for item in ["docker", "configs", ".github/workflows"]:
        if not (ROOT / item).exists():
            warns.append(f"缺少 {item}/（Stage1 可用 env+compose 等价，需在文档说明）")


def check_layers() -> None:
    """依赖方向：外层不得反向 import agents 内部实现。"""
    pattern = re.compile(r"(from|import)\s+app\.agents")
    for sub in ["routers", "services", "core", "db"]:
        for f in _py_files(APP / sub):
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if pattern.search(line) and "tools.base" not in line:
                    warns.append(f"跨层引用 agents：{f.relative_to(ROOT)}:{i} -> {line.strip()[:80]}")

    # agents 不得直接 import 缓存/DB 驱动（持久化必须走 services）
    for f in _py_files(APP / "agents"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"from app\.core\.cache|from app\.db\.session import engine", text):
            errors.append(f"agents 直接操作缓存/引擎：{f.relative_to(ROOT)}")


def check_code_rules() -> None:
    for f in _py_files(APP):
        text = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"except\s*:", text):
            errors.append(f"裸 except：{f.relative_to(ROOT)}")
        if "app/agents/prompts.py" in str(f).replace("\\", "/") and "PROMPT" in text:
            warns.append("prompt 仍是 Python 常量（agents/prompts.py），未抽 yaml —— Stage1 已知差距")

    hard = []
    for f in _py_files(APP):
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r"timeout\s*=\s*[0-9]+(?:\.[0-9]+)?", line) and "settings." not in line:
                hard.append(f"{f.relative_to(ROOT)}:{i}")
    if hard:
        warns.append(f"疑似硬编码超时 {len(hard)} 处（前 3：{', '.join(hard[:3])}）")


def check_assets() -> None:
    if (BACKEND / ".env").exists():
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "backend/.env"],
                cwd=ROOT, capture_output=True, text=True
            )
            if tracked.returncode == 0:
                errors.append("backend/.env 被 git 跟踪（密钥会进仓库）")
        except Exception:
            warns.append("无法执行 git 检查，跳过 .env 跟踪校验")

    big = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in {".zip", ".mp4", ".mov", ".bin"}
           and p.stat().st_size > 20 * 1024 * 1024]
    if big:
        warns.append(f"仓库内大体积二进制 {len(big)} 个（应走对象存储/DVC）")

    if (BACKEND / "storage").exists():
        print("[info] 本机 storage/ 存在（运行时生成，不提交 git）")


def check_ops() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8", errors="ignore")
    if "pgvector" not in compose:
        errors.append("compose 的 db 不是 pgvector 镜像，向量能力无法落地")
    if "healthcheck" not in compose:
        warns.append("compose 缺 backend healthcheck")
    if ".github/workflows/ci.yml" not in [str(p.relative_to(ROOT)).replace("\\", "/")
                                          for p in ROOT.rglob("ci.yml")]:
        warns.append("缺 CI workflow")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    for fn in (check_layout, check_layers, check_code_rules, check_assets, check_ops):
        fn()

    print("=" * 62)
    print("Harness 结构自检")
    print("=" * 62)
    for e in errors:
        print(f"[ERROR] {e}")
    for w in warns:
        print(f"[WARN ] {w}")
    if not errors and not warns:
        print("全部通过")
    print(f"\n合计：{len(errors)} ERROR / {len(warns)} WARN")
    if errors or (args.strict and warns):
        sys.exit(1)


if __name__ == "__main__":
    main()
