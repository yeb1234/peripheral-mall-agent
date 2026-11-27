"""
买家身份签名（HMAC-SHA256）。

要解决的问题：chat 接口原来直接信 body 里的 buyer_id。任何人改成 `buyer_demo`
就能拿到这个买家的订单上下文 —— 这是「用户 ID 裸传」的典型越权。

方案：
- `POST /api/auth/buyer` 用手机号后四位换 token（或 dev 模式下的演示白名单）；
- token = base64url(payload).base64url(hmac)，payload 含 buyer_id + 过期时间；
- chat 请求带 token 时，以 token 里的 buyer_id 为准，body 里的值只作展示；
- 生产（REQUIRE_BUYER_TOKEN=True）不带 token 直接拒绝，dev 放行并标记 unsigned。

密钥来自 settings.BUYER_TOKEN_SECRET；没配时用「应用名 + 环境」派生一个，
演示能跑，但部署时必须显式配置（文档里会写）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import re

from app.config import settings

_BUYER_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _secret() -> bytes:
    raw = settings.BUYER_TOKEN_SECRET or f"{settings.APP_NAME}:{settings.ENV}:buyer-v1"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def valid_buyer_id(buyer_id: str) -> bool:
    return bool(_BUYER_RE.match(buyer_id or ""))


def sign_buyer(buyer_id: str, ttl: int | None = None) -> str:
    if not valid_buyer_id(buyer_id):
        raise ValueError("买家 ID 只允许字母、数字、下划线、连字符，长度 ≤ 32")
    exp = int(time.time()) + int(ttl or settings.BUYER_TOKEN_TTL)
    payload = _b64e(json.dumps({"b": buyer_id, "e": exp}, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_buyer(token: str | None) -> str | None:
    """校验通过返回 buyer_id，否则 None（签名不对 / 过期 / 格式错）。"""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expect = _b64e(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        data = json.loads(_b64d(payload))
    except Exception:
        return None
    if int(data.get("e", 0)) < int(time.time()):
        return None
    buyer_id = str(data.get("b") or "")
    return buyer_id if valid_buyer_id(buyer_id) else None


def demo_buyer_allowed(buyer_id: str) -> bool:
    allowed = {x.strip() for x in (settings.DEMO_BUYERS or "").split(",") if x.strip()}
    return buyer_id in allowed or buyer_id.startswith("u_")
