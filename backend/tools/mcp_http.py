"""独立 MCP HTTP 服务（不依赖主站，别的项目可直接连 8001）。

运行：python -m tools.mcp_http
端点：http://127.0.0.1:8001/mcp  （MCP Streamable HTTP，POST JSON-RPC）

如果主站已经在跑，资源库也已挂在 http://127.0.0.1:8000/mcp；
这个独立入口用于「只想把资产库供出去、不想启动整个商城」的场景。
"""
from __future__ import annotations

import uvicorn

from tools.mcp_server import create_http_app

app = create_http_app(path="/mcp")


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")


if __name__ == "__main__":
    main()
