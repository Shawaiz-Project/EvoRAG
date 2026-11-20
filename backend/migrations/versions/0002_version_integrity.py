"""Protect version identity and audit history."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

IMMUTABLE_FIELDS = {
    "model_registry": ("id", "provider", "kind", "name", "version", "dimensions"),
    "embedding_spaces": ("id", "key", "version", "model_id", "dimensions", "metric", "recipe"),
    "rag_configurations": (
        "id",
        "key",
        "version",
        "strategy",
        "model_id",
        "embedding_space_id",
        "settings",
    ),
    "document_versions": (
        "id",
        "document_id",
        "version",
        "sha256",
        "size_bytes",
        "mime_type",
        "extracted_text",
        "attributes",
        "created_at",
    ),
    "document_chunks": (
        "id",
        "document_version_id",
        "parent_id",
        "ordinal",
        "content",
        "page",
        "section",
        "attributes",
        "created_at",
    ),
    "chunk_embeddings": ("id", "chunk_id", "embedding_space_id", "dimensions", "embedding"),
}


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION evorag_reject_history_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% history is immutable', TG_TABLE_NAME USING ERRCODE = '23514';
        END;
        $$
    """)
    for table, columns in IMMUTABLE_FIELDS.items():
        old = ", ".join(f"OLD.{field}" for field in columns)
        new = ", ".join(f"NEW.{field}" for field in columns)
        op.execute(f"""
            CREATE TRIGGER immutable_history BEFORE UPDATE ON {table}
            FOR EACH ROW WHEN (ROW({old}) IS DISTINCT FROM ROW({new}))
            EXECUTE FUNCTION evorag_reject_history_change()
        """)
    op.execute("""
        CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION evorag_reject_history_change()
    """)


def downgrade() -> None:
    for table in (*IMMUTABLE_FIELDS, "audit_logs"):
        op.execute(f"DROP TRIGGER immutable_history ON {table}")
    op.execute("DROP FUNCTION evorag_reject_history_change()")
