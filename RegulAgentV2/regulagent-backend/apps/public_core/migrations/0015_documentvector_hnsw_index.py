# Generated migration — adds HNSW vector index on DocumentVector.embedding
# Enables production-scale cosine similarity search via pgvector in PostgreSQL.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0014_add_workspace_to_work_products"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX documentvector_embedding_hnsw_idx
                ON public_core_document_vectors
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            """,
            reverse_sql="DROP INDEX IF EXISTS documentvector_embedding_hnsw_idx;",
        ),
    ]
