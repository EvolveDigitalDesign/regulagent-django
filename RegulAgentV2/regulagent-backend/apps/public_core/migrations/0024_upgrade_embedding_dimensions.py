"""Upgrade DocumentVector embeddings from 1536-dim (text-embedding-3-small) to 3072-dim (text-embedding-3-large).

Migration order:
1. Drop the existing HNSW/IVFFlat indexes
2. Delete all existing vectors (incompatible dimensions)
3. Alter the field to 3072 dimensions

Note: pgvector 0.8.x limits both HNSW and IVFFlat indexes to 2000 dimensions.
At current scale (~1000 vectors) exact scan is sufficient. Add an approximate
index after upgrading pgvector to 0.9+ or switching to halfvec.
"""

from django.db import migrations
import pgvector.django


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("public_core", "0023_w3_wizard_plan_import"),
    ]

    operations = [
        # 1. Drop existing indexes (dimension-specific, can't be reused)
        migrations.RunSQL(
            sql=(
                "DROP INDEX IF EXISTS documentvector_embedding_hnsw_idx; "
                "DROP INDEX IF EXISTS documentvector_embedding_ivfflat_idx;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 2. Delete all existing 1536-dim vectors (incompatible with 3072)
        migrations.RunSQL(
            sql="DELETE FROM public_core_document_vectors;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 3. Alter embedding field: 1536 → 3072 dimensions
        migrations.AlterField(
            model_name="documentvector",
            name="embedding",
            field=pgvector.django.VectorField(dimensions=3072),
        ),
        # No vector index created — pgvector 0.8.x limits HNSW/IVFFlat to 2000 dims.
        # Exact scan is fine at current scale. Revisit after pgvector upgrade.
    ]
