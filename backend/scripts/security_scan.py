"""
静态安全门禁（对应「前置扫描」里的自动化部分）。

扫的是「代码里能一眼看出问题的模式」，不是替代 SAST：
- SQL 拼接 / 命令执行 / 反序列化：Agent 应用最容易被工具参数带进去的几类；
- 硬编码密钥：演示项目最常见的翻车点；
- 前端 XSS 出口：dangerouslySetInnerHTML / innerHTML；
- 过宽 CORS：`allow_origins=["*"]` 配上凭证就是漏洞；
- 身份裸传：请求体里直接信 buyer_id。

用法：
    python scripts/security_scan.py            # 有人看报告
    python scripts/security_scan.py --strict   # 有 FAIL 就 exit 1（挂 CI）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = [ROOT / "app", ROOT.parent / "frontend" / "src"]
SKIP_PARTS = {"node_modules", "dist", ".venv", "__pycache__", ".git"}

RULES: list[tuple[str, str, str, str]] = [
    # (级别, 规则名, 正则, 说明)
    ("FAIL", "sql_concat", r'(execute|scalar)\s*\(\s*f["\']', "SQL 用 f-string 拼接，参数化查询可被绕过"),
    ("FAIL", "sql_format", r'(execute|scalar)\s*\(\s*["\'][^"\']*["\']\s*\.\s*format\s*\(', "SQL 用 format 拼接"),
    ("FAIL", "sql_percent", r'execute\s*\(\s*["\'][^"\']*%s[^"\']*["\']\s*%\s*\(', "SQL 用 % 拼接"),
    ("FAIL", "shell_true", r'subprocess\.\w+\([^)]*shell\s*=\s*True', "subprocess shell=True，命令注入风险"),
    ("FAIL", "os_system", r'os\.system\s*\(', "os.system 直接执行字符串"),
    ("FAIL", "eval_exec", r'(?<![.\w])(eval|exec)\s*\(\s*(?!.*compile\().+', "eval/exec 执行动态字符串"),
    ("FAIL", "pickle_loads", r'pickle\.loads?\s*\(', "反序列化不可信数据"),
    ("WARN", "yaml_load", r'yaml\.load\s*\(', "yaml.load 未指定 SafeLoader"),
    ("FAIL", "cors_wildcard", r'allow_origins\s*=\s*\[\s*["\']\*["\']\s*\]', "CORS 允许任意来源"),
    ("FAIL", "hardcoded_key", r'(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*["\'][A-Za-z0-9_\-/+=]{16,}["\']',
     "疑似硬编码密钥（应走环境变量）"),
    ("FAIL", "front_innerhtml", r'dangerouslySetInnerHTML|\.innerHTML\s*=', "前端直接写 HTML，XSS 出口"),
    ("WARN", "front_eval", r'(?<![.\w])(eval|new\s+Function)\s*\(', "前端动态执行"),
    ("WARN", "trust_buyer_body", r'buyer_id\s*[:=]\s*req\.buyer_id|buyer_id\s*=\s*req\.buyer_id', "直接信任请求体里的 buyer_id"),
    ("WARN", "debug_traceback", r'traceback\.print_exc|print\s*\(.*exception', "异常细节可能回显给用户"),
]

EXCLUDE_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".map"}


def iter_files():
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir() or p.suffix.lower() in EXCLUDE_SUFFIX:
                continue
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="有 FAIL 时返回非 0（CI 门禁）")
    args = ap.parse_args()

    findings: list[tuple[str, str, str, int, str]] = []
    scanned = 0
    for f in iter_files():
        scanned += 1
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if len(line) > 260:
                continue
            for level, name, pat, desc in RULES:
                if re.search(pat, line):
                    if name == "hardcoded_key" and ("os.getenv" in line or "settings." in line):
                        continue
                    findings.append((level, name, str(f.relative_to(ROOT.parent)), i, desc))

    fails = [x for x in findings if x[0] == "FAIL"]
    warns = [x for x in findings if x[0] == "WARN"]

    print(f"scanned files: {scanned}")
    print(f"FAIL: {len(fails)}   WARN: {len(warns)}\n")
    for level, name, path, line, desc in findings:
        print(f"[{level}] {name:18} {path}:{line}  {desc}")

    if not findings:
        print("no findings. ok.")
    return 1 if (args.strict and fails) else 0


if __name__ == "__main__":
    sys.exit(main())
