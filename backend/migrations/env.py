from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from app.database_connection import _sqlalchemy_url


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("Alembic 실행에는 DATABASE_URL이 필요합니다.")
config.set_main_option("sqlalchemy.url", _sqlalchemy_url(database_url).replace("%", "%%"))
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
