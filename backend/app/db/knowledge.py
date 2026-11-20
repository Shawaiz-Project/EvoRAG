from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Attributes, Base, Record


class Source(Record, Attributes, Base):
    __tablename__ = "sources"
    __table_args__ = (
        sa.UniqueConstraint("kind", "locator"),
        sa.CheckConstraint("kind IN ('file', 'website', 'github')", name="source_kind"),
    )

    kind: Mapped[str]
    locator: Mapped[str]
    title: Mapped[str]


class Document(Record, Attributes, Base):
    __tablename__ = "documents"
    __table_args__ = (sa.UniqueConstraint("source_id", "locator"),)

    source_id: Mapped[UUID] = mapped_column(sa.ForeignKey("sources.id"))
    locator: Mapped[str]
    title: Mapped[str]


class DocumentVersion(Record, Attributes, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "version"),
        sa.UniqueConstraint("document_id", "sha256"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        sa.CheckConstraint("version > 0 AND size_bytes >= 0", name="version_size"),
        sa.Index(
            "uq_document_versions_current",
            "document_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
        ),
    )

    document_id: Mapped[UUID] = mapped_column(sa.ForeignKey("documents.id"))
    version: Mapped[int]
    sha256: Mapped[str] = mapped_column(sa.String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    mime_type: Mapped[str]
    extracted_text: Mapped[str] = mapped_column(sa.Text)
    is_current: Mapped[bool] = mapped_column(server_default=sa.false())


class Chunk(Record, Attributes, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        sa.UniqueConstraint("document_version_id", "ordinal"),
        sa.UniqueConstraint("document_version_id", "id"),
        sa.ForeignKeyConstraint(
            ["document_version_id", "parent_id"],
            ["document_chunks.document_version_id", "document_chunks.id"],
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND (page IS NULL OR page > 0)"
            " AND (parent_id IS NULL OR parent_id <> id)",
            name="location",
        ),
        sa.Index("ix_document_chunks_search", "search_vector", postgresql_using="gin"),
    )

    document_version_id: Mapped[UUID] = mapped_column(sa.ForeignKey("document_versions.id"))
    parent_id: Mapped[UUID | None] = mapped_column(index=True)
    ordinal: Mapped[int]
    content: Mapped[str] = mapped_column(sa.Text)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
    )
    page: Mapped[int | None]
    section: Mapped[str | None]


class EmbeddingSpace(Record, Attributes, Base):
    __tablename__ = "embedding_spaces"
    __table_args__ = (
        sa.UniqueConstraint("key", "version"),
        sa.UniqueConstraint("id", "dimensions"),
        sa.ForeignKeyConstraint(
            ["model_id", "dimensions"], ["model_registry.id", "model_registry.dimensions"]
        ),
        sa.CheckConstraint(
            "version > 0 AND metric IN ('cosine', 'l2', 'inner_product')", name="version_metric"
        ),
    )

    key: Mapped[str]
    version: Mapped[int]
    model_id: Mapped[UUID] = mapped_column(index=True)
    dimensions: Mapped[int]
    metric: Mapped[str]
    recipe: Mapped[str]


class ChunkEmbedding(Record, Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        sa.UniqueConstraint("embedding_space_id", "chunk_id"),
        sa.ForeignKeyConstraint(
            ["embedding_space_id", "dimensions"],
            ["embedding_spaces.id", "embedding_spaces.dimensions"],
        ),
        sa.CheckConstraint("vector_dims(embedding) = dimensions", name="vector_dimensions"),
    )

    chunk_id: Mapped[UUID] = mapped_column(sa.ForeignKey("document_chunks.id"), index=True)
    embedding_space_id: Mapped[UUID]
    dimensions: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(VECTOR())
