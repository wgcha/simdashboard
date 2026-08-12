"""Store media bytes in PostgreSQL chunks and add drop-video references.

Revision ID: 0008_media_blob_storage
Revises: 0007_access_control_menu_policy
"""

from __future__ import annotations

from alembic import op


revision = "0008_media_blob_storage"
down_revision = "0007_access_control_menu_policy"
branch_labels = None
depends_on = None


def _add_constraint_if_missing(name: str, table: str, definition: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{name}' AND conrelid = '{table}'::regclass
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} {definition};
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_blobs (
            id UUID PRIMARY KEY,
            sha256 CHAR(64) NOT NULL,
            file_size BIGINT NOT NULL CHECK (file_size >= 0),
            chunk_size INTEGER NOT NULL DEFAULT 1048576
                CHECK (chunk_size > 0 AND chunk_size <= 1048576),
            chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            orphaned_at TIMESTAMPTZ,
            CONSTRAINT asset_blobs_sha256_format CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT asset_blobs_identity UNIQUE (sha256, file_size)
        );

        CREATE TABLE IF NOT EXISTS asset_blob_chunks (
            blob_id UUID NOT NULL,
            chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
            content BYTEA NOT NULL,
            content_length INTEGER NOT NULL,
            content_sha256 CHAR(64) NOT NULL,
            PRIMARY KEY (blob_id, chunk_index),
            CONSTRAINT asset_blob_chunks_size CHECK (content_length = octet_length(content)),
            CONSTRAINT asset_blob_chunks_max_size CHECK (content_length > 0 AND content_length <= 1048576),
            CONSTRAINT asset_blob_chunks_sha256_format CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
        );

        ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS blob_id UUID;
        ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS original_filename TEXT;
        ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS mime_type TEXT;

        CREATE TABLE IF NOT EXISTS drop_video_assets (
            video_id VARCHAR PRIMARY KEY,
            load_case_id VARCHAR NOT NULL,
            blob_id UUID NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            scene_name TEXT NOT NULL,
            sort_order INTEGER NOT NULL CHECK (sort_order > 0),
            metadata_json JSONB,
            CONSTRAINT ux_drop_video_assets_case_order UNIQUE (load_case_id, sort_order)
        );

        CREATE INDEX IF NOT EXISTS ix_media_assets_blob_id ON media_assets(blob_id);
        CREATE INDEX IF NOT EXISTS ix_drop_video_assets_blob_id ON drop_video_assets(blob_id);
        CREATE INDEX IF NOT EXISTS ix_drop_video_assets_load_case ON drop_video_assets(load_case_id, sort_order);
        """
    )
    _add_constraint_if_missing("fk_media_assets_blob", "media_assets", "FOREIGN KEY (blob_id) REFERENCES asset_blobs(id)")
    _add_constraint_if_missing(
        "fk_asset_blob_chunks_blob",
        "asset_blob_chunks",
        "FOREIGN KEY (blob_id) REFERENCES asset_blobs(id) ON DELETE CASCADE",
    )
    _add_constraint_if_missing(
        "fk_drop_video_assets_load_case",
        "drop_video_assets",
        "FOREIGN KEY (load_case_id) REFERENCES load_cases(id)",
    )
    _add_constraint_if_missing(
        "fk_drop_video_assets_blob",
        "drop_video_assets",
        "FOREIGN KEY (blob_id) REFERENCES asset_blobs(id) ON DELETE RESTRICT",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE drop_video_assets DROP CONSTRAINT IF EXISTS fk_drop_video_assets_blob")
    op.execute("ALTER TABLE drop_video_assets DROP CONSTRAINT IF EXISTS fk_drop_video_assets_load_case")
    op.execute("ALTER TABLE asset_blob_chunks DROP CONSTRAINT IF EXISTS fk_asset_blob_chunks_blob")
    op.execute("ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS fk_media_assets_blob")
    op.execute("DROP INDEX IF EXISTS ix_drop_video_assets_load_case")
    op.execute("DROP INDEX IF EXISTS ix_drop_video_assets_blob_id")
    op.execute("DROP INDEX IF EXISTS ix_media_assets_blob_id")
    op.execute("DROP TABLE IF EXISTS drop_video_assets")
    op.execute("ALTER TABLE media_assets DROP COLUMN IF EXISTS original_filename")
    op.execute("ALTER TABLE media_assets DROP COLUMN IF EXISTS blob_id")
    op.execute("DROP TABLE IF EXISTS asset_blob_chunks")
    op.execute("DROP TABLE IF EXISTS asset_blobs")
