from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Attributes, Base, Record


class User(Record, Attributes, Base):
    __tablename__ = "users"
    __table_args__ = (
        sa.Index("uq_users_email_normalized", sa.func.lower(sa.column("email")), unique=True),
    )

    email: Mapped[str] = mapped_column(sa.String(320))
    display_name: Mapped[str]
    enabled: Mapped[bool] = mapped_column(server_default=sa.true())
    password_hash: Mapped[str | None]


class Role(Record, Base):
    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(sa.String(64), unique=True)
    description: Mapped[str]


class UserRole(Record, Base):
    __tablename__ = "user_roles"
    __table_args__ = (sa.UniqueConstraint("user_id", "role_id"),)

    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[UUID] = mapped_column(sa.ForeignKey("roles.id", ondelete="CASCADE"), index=True)


class AuthSession(Record, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class ModelArtifact(Record, Attributes, Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        sa.UniqueConstraint("provider", "kind", "name", "version"),
        sa.UniqueConstraint("id", "dimensions"),
        sa.CheckConstraint(
            "(kind = 'embedding' AND dimensions IS NOT NULL AND dimensions BETWEEN 1 AND 16000)"
            " OR (kind IN ('llm', 'reranker') AND dimensions IS NULL)",
            name="kind_dimensions",
        ),
    )

    provider: Mapped[str]
    kind: Mapped[str]
    name: Mapped[str]
    version: Mapped[str]
    dimensions: Mapped[int | None]
