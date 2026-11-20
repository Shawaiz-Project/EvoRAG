"""Keep the embedding history trigger valid with pg_restore's empty search path."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def replace_trigger(*, cast_embedding: bool) -> None:
    embedding = "::text" if cast_embedding else ""
    op.execute("DROP TRIGGER immutable_history ON chunk_embeddings")
    op.execute(f"""
        CREATE TRIGGER immutable_history BEFORE UPDATE ON chunk_embeddings
        FOR EACH ROW WHEN (
            ROW(OLD.id, OLD.chunk_id, OLD.embedding_space_id, OLD.dimensions,
                OLD.embedding{embedding})
            IS DISTINCT FROM
            ROW(NEW.id, NEW.chunk_id, NEW.embedding_space_id, NEW.dimensions,
                NEW.embedding{embedding})
        )
        EXECUTE FUNCTION evorag_reject_history_change()
    """)


def upgrade() -> None:
    replace_trigger(cast_embedding=True)


def downgrade() -> None:
    replace_trigger(cast_embedding=False)
