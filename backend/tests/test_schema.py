"""The migrations build exactly the schema the models describe: a model change
without its migration (or the reverse) fails here, not on the real database."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

import app.models  # noqa: F401  (every table on Base.metadata)
from app.db import Base
from app.migrate import upgrade_database


def test_migrations_build_exactly_the_models(env):
    url = f"sqlite:///{(env / 'schema.db').as_posix()}"
    upgrade_database(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={
                "compare_type": True,
                # the FTS5 table and its shadow tables are created by hand in a migration
                "include_object": lambda obj, name, type_, reflected, compare_to: not (
                    type_ == "table" and name and name.startswith("chunks_fts")),
            })
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()
