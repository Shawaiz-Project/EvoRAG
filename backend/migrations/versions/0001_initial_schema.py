"""Create the initial EvoRAG schema."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "benchmark_datasets",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_benchmark_datasets_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_datasets")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_benchmark_datasets_key_version")),
    )
    op.create_table(
        "model_registry",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'embedding' AND dimensions IS NOT NULL AND dimensions BETWEEN 1 AND 16000)"
            " OR (kind IN ('llm', 'reranker') AND dimensions IS NULL)",
            name=op.f("ck_model_registry_kind_dimensions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_registry")),
        sa.UniqueConstraint("id", "dimensions", name=op.f("uq_model_registry_id_dimensions")),
        sa.UniqueConstraint(
            "provider",
            "kind",
            "name",
            "version",
            name=op.f("uq_model_registry_provider_kind_name_version"),
        ),
    )
    op.create_table(
        "roles",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("key", name=op.f("uq_roles_key")),
    )
    op.create_table(
        "sources",
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("locator", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('file', 'website', 'github')", name=op.f("ck_sources_source_kind")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("kind", "locator", name=op.f("uq_sources_kind_locator")),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email_normalized", "users", [sa.literal_column("lower(email)")], unique=True
    )
    op.create_table(
        "audit_logs",
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_logs_actor_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        op.f("ix_audit_logs_actor_user_id"), "audit_logs", ["actor_user_id"], unique=False
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_logs_subject_history",
        "audit_logs",
        ["subject_type", "subject_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "benchmark_cases",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("golden_answer", sa.Text(), nullable=True),
        sa.Column("expected_verdict", sa.String(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["benchmark_datasets.id"],
            name=op.f("fk_benchmark_cases_dataset_id_benchmark_datasets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_cases")),
        sa.UniqueConstraint("dataset_id", "id", name=op.f("uq_benchmark_cases_dataset_id_id")),
        sa.UniqueConstraint("dataset_id", "key", name=op.f("uq_benchmark_cases_dataset_id_key")),
    )
    op.create_table(
        "conversations",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_conversations_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_owner_history", "conversations", ["owner_id", "created_at"], unique=False
    )
    op.create_table(
        "documents",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("locator", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_documents_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("source_id", "locator", name=op.f("uq_documents_source_id_locator")),
    )
    op.create_table(
        "embedding_spaces",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("recipe", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version > 0 AND metric IN ('cosine', 'l2', 'inner_product')",
            name=op.f("ck_embedding_spaces_version_metric"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id", "dimensions"],
            ["model_registry.id", "model_registry.dimensions"],
            name=op.f("fk_embedding_spaces_model_id_dimensions_model_registry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_spaces")),
        sa.UniqueConstraint("id", "dimensions", name=op.f("uq_embedding_spaces_id_dimensions")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_embedding_spaces_key_version")),
    )
    op.create_index(
        op.f("ix_embedding_spaces_model_id"), "embedding_spaces", ["model_id"], unique=False
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name=op.f("fk_user_roles_role_id_roles"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_roles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        sa.UniqueConstraint("user_id", "role_id", name=op.f("uq_user_roles_user_id_role_id")),
    )
    op.create_index(op.f("ix_user_roles_role_id"), "user_roles", ["role_id"], unique=False)
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_versions_sha256")),
        sa.CheckConstraint(
            "version > 0 AND size_bytes >= 0", name=op.f("ck_document_versions_version_size")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_versions_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "document_id", "sha256", name=op.f("uq_document_versions_document_id_sha256")
        ),
        sa.UniqueConstraint(
            "document_id", "version", name=op.f("uq_document_versions_document_id_version")
        ),
    )
    op.create_index(
        op.f("ix_document_versions_sha256"), "document_versions", ["sha256"], unique=False
    )
    op.create_index(
        "uq_document_versions_current",
        "document_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_messages_role")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("conversation_id", "id", name=op.f("uq_messages_conversation_id_id")),
    )
    op.create_index(
        "ix_messages_conversation_history",
        "messages",
        ["conversation_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "rag_configurations",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(), nullable=False),
        sa.Column("embedding_space_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.Uuid(), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_rag_configurations_version")),
        sa.ForeignKeyConstraint(
            ["embedding_space_id"],
            ["embedding_spaces.id"],
            name=op.f("fk_rag_configurations_embedding_space_id_embedding_spaces"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["model_registry.id"],
            name=op.f("fk_rag_configurations_model_id_model_registry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_configurations")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_rag_configurations_key_version")),
    )
    op.create_index(
        op.f("ix_rag_configurations_embedding_space_id"),
        "rag_configurations",
        ["embedding_space_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rag_configurations_model_id"), "rag_configurations", ["model_id"], unique=False
    )
    op.create_table(
        "document_chunks",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
            nullable=False,
        ),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND (page IS NULL OR page > 0)"
            " AND (parent_id IS NULL OR parent_id <> id)",
            name=op.f("ck_document_chunks_location"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "parent_id"],
            ["document_chunks.document_version_id", "document_chunks.id"],
            name=op.f("fk_document_chunks_document_version_id_parent_id_document_chunks"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_document_chunks_document_version_id_document_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "document_version_id", "id", name=op.f("uq_document_chunks_document_version_id_id")
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "ordinal",
            name=op.f("uq_document_chunks_document_version_id_ordinal"),
        ),
    )
    op.create_index(
        op.f("ix_document_chunks_parent_id"), "document_chunks", ["parent_id"], unique=False
    )
    op.create_index(
        "ix_document_chunks_search",
        "document_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "rag_runs",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("answer_id", sa.Uuid(), nullable=True),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('primary', 'teacher', 'benchmark')"
            " AND status IN ('running', 'completed', 'abstained', 'failed')",
            name=op.f("ck_rag_runs_role_status"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at", name=op.f("ck_rag_runs_duration")
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["rag_configurations.id"],
            name=op.f("fk_rag_runs_configuration_id_rag_configurations"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "answer_id"],
            ["messages.conversation_id", "messages.id"],
            name=op.f("fk_rag_runs_conversation_id_answer_id_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "question_id"],
            ["messages.conversation_id", "messages.id"],
            name=op.f("fk_rag_runs_conversation_id_question_id_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_rag_runs_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["rag_runs.id"], name=op.f("fk_rag_runs_parent_run_id_rag_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_runs")),
    )
    op.create_index(op.f("ix_rag_runs_answer_id"), "rag_runs", ["answer_id"], unique=False)
    op.create_index(
        op.f("ix_rag_runs_configuration_id"), "rag_runs", ["configuration_id"], unique=False
    )
    op.create_index(
        "ix_rag_runs_conversation_history",
        "rag_runs",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_rag_runs_parent_run_id"), "rag_runs", ["parent_run_id"], unique=False)
    op.create_index(op.f("ix_rag_runs_question_id"), "rag_runs", ["question_id"], unique=False)
    op.create_table(
        "benchmark_evidence",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("relevance", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("relevance > 0", name=op.f("ck_benchmark_evidence_relevance")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["benchmark_cases.id"],
            name=op.f("fk_benchmark_evidence_case_id_benchmark_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_benchmark_evidence_chunk_id_document_chunks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_evidence")),
        sa.UniqueConstraint(
            "case_id", "chunk_id", name=op.f("uq_benchmark_evidence_case_id_chunk_id")
        ),
    )
    op.create_index(
        op.f("ix_benchmark_evidence_chunk_id"), "benchmark_evidence", ["chunk_id"], unique=False
    )
    op.create_table(
        "benchmark_results",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("experiment_key", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["rag_configurations.id"],
            name=op.f("fk_benchmark_results_configuration_id_rag_configurations"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "case_id"],
            ["benchmark_cases.dataset_id", "benchmark_cases.id"],
            name=op.f("fk_benchmark_results_dataset_id_case_id_benchmark_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["benchmark_datasets.id"],
            name=op.f("fk_benchmark_results_dataset_id_benchmark_datasets"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["rag_runs.id"], name=op.f("fk_benchmark_results_run_id_rag_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_results")),
    )
    op.create_index(
        op.f("ix_benchmark_results_case_id"), "benchmark_results", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_benchmark_results_configuration_id"),
        "benchmark_results",
        ["configuration_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_benchmark_results_experiment_key"),
        "benchmark_results",
        ["experiment_key"],
        unique=False,
    )
    op.create_index(
        "ix_benchmark_results_history",
        "benchmark_results",
        ["dataset_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_benchmark_results_run_id"), "benchmark_results", ["run_id"], unique=False
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_space_id", sa.Uuid(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimensions",
            name=op.f("ck_chunk_embeddings_vector_dimensions"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_document_chunks"),
        ),
        sa.ForeignKeyConstraint(
            ["embedding_space_id", "dimensions"],
            ["embedding_spaces.id", "embedding_spaces.dimensions"],
            name=op.f("fk_chunk_embeddings_embedding_space_id_dimensions_embedding_spaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint(
            "embedding_space_id",
            "chunk_id",
            name=op.f("uq_chunk_embeddings_embedding_space_id_chunk_id"),
        ),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_chunk_id"), "chunk_embeddings", ["chunk_id"], unique=False
    )
    op.create_table(
        "retrieval_results",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("iteration", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rank > 0 AND iteration >= 0", name=op.f("ck_retrieval_results_rank_iteration")
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_retrieval_results_chunk_id_document_chunks"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["rag_runs.id"], name=op.f("fk_retrieval_results_run_id_rag_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_results")),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "iteration",
            "chunk_id",
            name=op.f("uq_retrieval_results_run_id_stage_iteration_chunk_id"),
        ),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "iteration",
            "rank",
            name=op.f("uq_retrieval_results_run_id_stage_iteration_rank"),
        ),
    )
    op.create_index(
        op.f("ix_retrieval_results_chunk_id"), "retrieval_results", ["chunk_id"], unique=False
    )
    op.create_table(
        "teacher_evaluations",
        sa.Column("primary_run_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_run_id", sa.Uuid(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "primary_run_id <> teacher_run_id AND verdict IN ('pass', 'revise', 'reject')",
            name=op.f("ck_teacher_evaluations_distinct_runs_verdict"),
        ),
        sa.ForeignKeyConstraint(
            ["primary_run_id"],
            ["rag_runs.id"],
            name=op.f("fk_teacher_evaluations_primary_run_id_rag_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["teacher_run_id"],
            ["rag_runs.id"],
            name=op.f("fk_teacher_evaluations_teacher_run_id_rag_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teacher_evaluations")),
        sa.UniqueConstraint("teacher_run_id", name=op.f("uq_teacher_evaluations_teacher_run_id")),
    )
    op.create_index(
        op.f("ix_teacher_evaluations_primary_run_id"),
        "teacher_evaluations",
        ["primary_run_id"],
        unique=False,
    )
    op.create_table(
        "user_feedback",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating IS NOT NULL OR comment IS NOT NULL OR correction IS NOT NULL",
            name=op.f("ck_user_feedback_content"),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 5", name=op.f("ck_user_feedback_rating")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["rag_runs.id"], name=op.f("fk_user_feedback_run_id_rag_runs")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_feedback_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_feedback")),
        sa.UniqueConstraint("run_id", "user_id", name=op.f("uq_user_feedback_run_id_user_id")),
    )
    op.create_index(op.f("ix_user_feedback_user_id"), "user_feedback", ["user_id"], unique=False)
    op.create_table(
        "learning_proposals",
        sa.Column("author_kind", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("feedback_id", sa.Uuid(), nullable=True),
        sa.Column("evaluation_id", sa.Uuid(), nullable=True),
        sa.Column("target_version_id", sa.Uuid(), nullable=True),
        sa.Column("published_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("proposed_change", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(author_kind = 'ai' AND author_user_id IS NULL)"
            " OR (author_kind = 'human' AND author_user_id IS NOT NULL)",
            name=op.f("ck_learning_proposals_author"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','under_review','approved','rejected','published','rolled_back')",
            name=op.f("ck_learning_proposals_status"),
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            name=op.f("fk_learning_proposals_author_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["teacher_evaluations.id"],
            name=op.f("fk_learning_proposals_evaluation_id_teacher_evaluations"),
        ),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["user_feedback.id"],
            name=op.f("fk_learning_proposals_feedback_id_user_feedback"),
        ),
        sa.ForeignKeyConstraint(
            ["published_version_id"],
            ["document_versions.id"],
            name=op.f("fk_learning_proposals_published_version_id_document_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["target_version_id"],
            ["document_versions.id"],
            name=op.f("fk_learning_proposals_target_version_id_document_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_proposals")),
    )
    op.create_index(
        op.f("ix_learning_proposals_author_user_id"),
        "learning_proposals",
        ["author_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_learning_proposals_evaluation_id"),
        "learning_proposals",
        ["evaluation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_learning_proposals_feedback_id"),
        "learning_proposals",
        ["feedback_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_learning_proposals_published_version_id"),
        "learning_proposals",
        ["published_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_proposals_queue", "learning_proposals", ["status", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_learning_proposals_target_version_id"),
        "learning_proposals",
        ["target_version_id"],
        unique=False,
    )
    op.create_table(
        "learning_approvals",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')", name=op.f("ck_learning_approvals_decision")
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["learning_proposals.id"],
            name=op.f("fk_learning_approvals_proposal_id_learning_proposals"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name=op.f("fk_learning_approvals_reviewer_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_approvals")),
    )
    op.create_index(
        "ix_learning_approvals_history",
        "learning_approvals",
        ["proposal_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_learning_approvals_reviewer_id"),
        "learning_approvals",
        ["reviewer_id"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_learning_approvals_reviewer_id"), table_name="learning_approvals")
    op.drop_index("ix_learning_approvals_history", table_name="learning_approvals")
    op.drop_table("learning_approvals")
    op.drop_index(op.f("ix_learning_proposals_target_version_id"), table_name="learning_proposals")
    op.drop_index("ix_learning_proposals_queue", table_name="learning_proposals")
    op.drop_index(
        op.f("ix_learning_proposals_published_version_id"), table_name="learning_proposals"
    )
    op.drop_index(op.f("ix_learning_proposals_feedback_id"), table_name="learning_proposals")
    op.drop_index(op.f("ix_learning_proposals_evaluation_id"), table_name="learning_proposals")
    op.drop_index(op.f("ix_learning_proposals_author_user_id"), table_name="learning_proposals")
    op.drop_table("learning_proposals")
    op.drop_index(op.f("ix_user_feedback_user_id"), table_name="user_feedback")
    op.drop_table("user_feedback")
    op.drop_index(op.f("ix_teacher_evaluations_primary_run_id"), table_name="teacher_evaluations")
    op.drop_table("teacher_evaluations")
    op.drop_index(op.f("ix_retrieval_results_chunk_id"), table_name="retrieval_results")
    op.drop_table("retrieval_results")
    op.drop_index(op.f("ix_chunk_embeddings_chunk_id"), table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index(op.f("ix_benchmark_results_run_id"), table_name="benchmark_results")
    op.drop_index("ix_benchmark_results_history", table_name="benchmark_results")
    op.drop_index(op.f("ix_benchmark_results_experiment_key"), table_name="benchmark_results")
    op.drop_index(op.f("ix_benchmark_results_configuration_id"), table_name="benchmark_results")
    op.drop_index(op.f("ix_benchmark_results_case_id"), table_name="benchmark_results")
    op.drop_table("benchmark_results")
    op.drop_index(op.f("ix_benchmark_evidence_chunk_id"), table_name="benchmark_evidence")
    op.drop_table("benchmark_evidence")
    op.drop_index(op.f("ix_rag_runs_question_id"), table_name="rag_runs")
    op.drop_index(op.f("ix_rag_runs_parent_run_id"), table_name="rag_runs")
    op.drop_index("ix_rag_runs_conversation_history", table_name="rag_runs")
    op.drop_index(op.f("ix_rag_runs_configuration_id"), table_name="rag_runs")
    op.drop_index(op.f("ix_rag_runs_answer_id"), table_name="rag_runs")
    op.drop_table("rag_runs")
    op.drop_index("ix_document_chunks_search", table_name="document_chunks", postgresql_using="gin")
    op.drop_index(op.f("ix_document_chunks_parent_id"), table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(op.f("ix_rag_configurations_model_id"), table_name="rag_configurations")
    op.drop_index(op.f("ix_rag_configurations_embedding_space_id"), table_name="rag_configurations")
    op.drop_table("rag_configurations")
    op.drop_index("ix_messages_conversation_history", table_name="messages")
    op.drop_table("messages")
    op.drop_index(
        "uq_document_versions_current",
        table_name="document_versions",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index(op.f("ix_document_versions_sha256"), table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index(op.f("ix_user_roles_role_id"), table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index(op.f("ix_embedding_spaces_model_id"), table_name="embedding_spaces")
    op.drop_table("embedding_spaces")
    op.drop_table("documents")
    op.drop_index("ix_conversations_owner_history", table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("benchmark_cases")
    op.drop_index("ix_audit_logs_subject_history", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_user_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("uq_users_email_normalized", table_name="users")
    op.drop_table("users")
    op.drop_table("sources")
    op.drop_table("roles")
    op.drop_table("model_registry")
    op.drop_table("benchmark_datasets")
    # ### end Alembic commands ###
