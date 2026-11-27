"""请求体模型（Pydantic）。响应多直接返回 dict，保持轻量。

入参一律加长度/正则约束：这是「前置扫描」的第一道 —— 超长文本、非法 ID、
控制字符在进入业务逻辑之前就被 Pydantic 拦掉。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    buyer_id: str = Field(default="buyer_demo", max_length=32, pattern=r"^[A-Za-z0-9_-]*$")
    channel: str = Field(default="web", max_length=16)
    message: str = Field(..., min_length=1, max_length=2000)
    token: str | None = Field(default=None, max_length=512)     # buyer 身份签名
    request_id: str | None = Field(default=None, max_length=64)  # 前端请求去重/排障用


class AuthRequest(BaseModel):
    buyer_id: str = Field(default="buyer_demo", max_length=32, pattern=r"^[A-Za-z0-9_-]*$")
    phone_last4: str | None = Field(default=None, pattern=r"^\d{4}$")


class VerifyRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    phone_last4: str = Field(..., pattern=r"^\d{4}$")


class ConfirmRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    confirm_id: str = Field(..., max_length=64)
    approve: bool


class CheckoutLine(BaseModel):
    product_id: str | None = None
    sku: str | None = None
    qty: int = 1


class Address(BaseModel):
    province: str
    city: str
    district: str
    detail: str
    contact: str
    phone: str


class CreateOrderRequest(BaseModel):
    buyer_id: str = "buyer_demo"
    lines: list[CheckoutLine]
    address: Address
    # 有签名 token 时以 token 内的 buyer_id 为准，body 的值只作展示
    token: str | None = Field(default=None, max_length=512)
