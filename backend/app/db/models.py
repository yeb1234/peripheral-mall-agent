"""
ORM 模型。

域模型（与电商客服 Agent 设计文档一一对应）：
- Product / Driver：商品与驱动（驱动下载是外设场景的核心差异化能力）
- Order / OrderItem：订单与明细（写操作的「真实对象」）
- Session / Message / OrderBinding：会话、消息、订单绑定（身份闸的来源）
- Ticket：工单（写操作的本地落库，事务边界第一笔）
- Audit：审计（只追加不改，四类事件必留痕）
- PolicyRule：结构化政策（退换 / 保修 / 时效，策略引擎直接判定，不让模型推理）
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"  # 待支付
    PAID = "paid"                        # 已支付 / 待发货
    SHIPPED = "shipped"                  # 已发货
    DELIVERED = "delivered"              # 已签收
    DONE = "done"                        # 已完成
    CANCELLED = "cancelled"              # 已取消
    REFUNDING = "refunding"              # 退款中
    REFUNDED = "refunded"                # 已退款


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float)
    spec_snapshot: Mapped[str] = mapped_column(Text, default="{}")  # JSON：下单时的规格快照


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    buyer_id: Mapped[str] = mapped_column(String(64), index=True)
    # 仅存哈希：手机号绝不进明文上下文（PII 最小化）
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.PENDING_PAYMENT, index=True)

    # 收货信息（结构化地址对象，改地址工具消费的正是它）
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[str] = mapped_column(String(200), default="")
    contact: Mapped[str] = mapped_column(String(32), default="")

    total: Mapped[float] = mapped_column(Float, default=0.0)
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # 物流
    carrier: Mapped[str] = mapped_column(String(32), default="")
    tracking_no: Mapped[str] = mapped_column(String(64), default="", index=True)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates=None, cascade="all, delete-orphan", lazy="selectin"
    )

    def to_public_dict(self) -> dict:
        """出参裁剪：地址脱敏、单号仅露后四位、不泄露支付/成本信息。"""
        return {
            "order_no_tail": self.order_no[-4:],
            "status": self.status.value,
            "total": self.total,
            "items": [
                {"name": i.name, "sku": i.sku, "qty": i.qty, "unit_price": i.unit_price}
                for i in self.items
            ],
            "address_masked": self._masked_address(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "carrier": self.carrier,
            "tracking_no": self.tracking_no[-4:] if self.tracking_no else "",
        }

    def _masked_address(self) -> str:
        detail = self.detail
        if len(detail) > 6:
            detail = detail[:2] + "***" + detail[-2:]
        return f"{self.province}{self.city}{self.district}{detail}"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    brand: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)  # keyboard/mouse/headset/webcam/dock/gamepad
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    stock: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    # 结构化规格：接口/兼容/尺寸/重量——售前问答直接命中字段，绝不「读文档猜参数」
    specs: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    image: Mapped[str] = mapped_column(String(256), default="")
    featured: Mapped[bool] = mapped_column(Boolean, default=False)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(32))
    os: Mapped[str] = mapped_column(String(16), index=True)  # Windows/macOS/Linux
    arch: Mapped[str] = mapped_column(String(16), default="x64")
    size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    release_date: Mapped[str] = mapped_column(String(16), default="")
    changelog: Mapped[str] = mapped_column(Text, default="")
    file_url: Mapped[str] = mapped_column(String(256), default="")  # 演示用占位下载地址
    sha256: Mapped[str] = mapped_column(String(64), default="")


class Manual(Base):
    """说明书：每个型号对应一份或多份用户手册（含语言版本）。"""

    __tablename__ = "manuals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    lang: Mapped[str] = mapped_column(String(8), default="zh")  # zh / en
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    release_date: Mapped[str] = mapped_column(String(16), default="")
    file_url: Mapped[str] = mapped_column(String(256), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")


class Resource(Base):
    """其他可下载资源：快速入门卡 / FAQ / 保修卡 / 配套软件说明等。"""

    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="guide")  # quick_start / faq / warranty / software
    size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    release_date: Mapped[str] = mapped_column(String(16), default="")
    file_url: Mapped[str] = mapped_column(String(256), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")


class FileAsset(Base):
    """文件资产元数据：文档/驱动的正文落在 STORAGE_DIR，库里只存索引与校验值。

    对象存储（S3/MinIO）接入时，把 rel_path 换成 object_key 即可，表结构不变；
    业务库永远不存文件二进制。
    """

    __tablename__ = "file_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16), index=True)       # manual/resource/driver
    product_sku: Mapped[str] = mapped_column(String(64), index=True, default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(200), default="")
    rel_path: Mapped[str] = mapped_column(String(256), default="")
    mime: Mapped[str] = mapped_column(String(64), default="text/markdown")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    source_kind: Mapped[str] = mapped_column(String(16), default="")  # 与 knowledge_docs 对齐
    source_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now, onupdate=dt.datetime.now)


class KnowledgeDoc(Base):
    """说明书知识库切片：Manual / Resource 的「章节级」检索单元。

    由 knowledge_service 自动同步写入：来源说明书正文按章节切片后入库，
    与下载文件共用同一份正文（single source of truth）。
    content_sha256 是「来源全文」的指纹，内容未变则不重建（幂等），
    内容变更则整体替换该来源的全部切片，避免残留旧版本知识。
    """

    __tablename__ = "knowledge_docs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source_kind: Mapped[str] = mapped_column(String(16), index=True)   # manual / resource
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    product_sku: Mapped[str] = mapped_column(String(64), index=True)
    product_name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(32), default="")
    doc_title: Mapped[str] = mapped_column(String(200), default="")
    doc_kind: Mapped[str] = mapped_column(String(32), default="")       # manual / quick_start / warranty…
    section_no: Mapped[int] = mapped_column(Integer, default=0)
    section_title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    search_text: Mapped[str] = mapped_column(Text, default="")          # SKU + 标题 + 正文，供召回
    file_url: Mapped[str] = mapped_column(String(256), default="")      # 引用回原始下载文件
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    buyer_id: Mapped[str] = mapped_column(String(64), default="anonymous", index=True)
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web | platform | wecom
    state_json: Mapped[str] = mapped_column(Text, default="{}")  # 当前订单快照 + 意图摘要
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)  # 会话级串行写锁标记


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[str] = mapped_column(Text, default="{}")  # JSON：工具调用、确认卡等
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    __table_args__ = (UniqueConstraint("session_id", "id", name="uq_msg_session"),)


class OrderBinding(Base):
    __tablename__ = "order_bindings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    relation: Mapped[str] = mapped_column(String(16), default="recent")  # recent | verified
    bound_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    __table_args__ = (UniqueConstraint("session_id", "order_id", name="uq_bind"),)


class Ticket(Base):
    """工单：写操作本地先落库（事务边界第一笔），再调外部接口。"""
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    ticket_type: Mapped[str] = mapped_column(String(32))  # address_change / cancel / aftersale / compensation
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | done | failed | rolled_back
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    external_ref: Mapped[str] = mapped_column(String(64), default="")  # 外部接口回执
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Audit(Base):
    """审计：只追加不改。四类事件必留痕。"""
    __tablename__ = "audits"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    order_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)  # session_bind | tool_call | write_exec | handoff
    actor: Mapped[str] = mapped_column(String(16), default="ai")  # ai | human
    input: Mapped[str] = mapped_column(Text, default="{}")
    output: Mapped[str] = mapped_column(Text, default="{}")
    decision: Mapped[str] = mapped_column(Text, default="")  # 策略判定结果
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)


class PolicyRule(Base):
    """结构化政策：策略引擎直接判定，不让模型推理。"""
    __tablename__ = "policy_rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # return | warranty | shipping
    category: Mapped[str] = mapped_column(String(32), index=True)  # 适用商品品类
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON：天数/月数/额度/条件/例外
    effective_from: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# 记忆系统：长期用户画像 / 行为事件流 / 多任务断点
# 与「会话内短期状态（Session.state_json / LangGraph state）」分层：
#   UserProfile  = 跨会话、跨设备可恢复的长期事实（习惯、偏好、统计）
#   UserEvent    = 只追加的行为流，既能算习惯，也能给客服解释「为什么推荐这个」
#   AgentTask    = 一个诉求 = 一个任务；确认卡挂起即 checkpoint，可跨会话续点
# ---------------------------------------------------------------------------
class UserProfile(Base):
    __tablename__ = "user_profiles"

    buyer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    prefs_json: Mapped[str] = mapped_column(Text, default="{}")   # 习惯/偏好（手型、有线无线、ANC…）
    stats_json: Mapped[str] = mapped_column(Text, default="{}")   # 统计（浏览/加购/下单/客服轮次…）
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class UserEvent(Base):
    __tablename__ = "user_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    buyer_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    task_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)     # view_product | add_to_cart | order_created | chat | flash_reserve …
    ref_sku: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(String(240), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    buyer_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    stage: Mapped[str] = mapped_column(String(16), index=True)    # presale | insale | aftersale | chitchat | handoff
    title: Mapped[str] = mapped_column(String(200), default="")
    focus_sku: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | paused | done
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")             # 续点：确认卡/上次工具/参数
    turns: Mapped[int] = mapped_column(Integer, default=0)
    last_message: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
