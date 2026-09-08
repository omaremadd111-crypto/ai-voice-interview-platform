from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from config.settings import load_settings
from services.db import orm_models  # noqa: F401  -- registers all tables on Base.metadata
from services.db.orm_base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# disable_existing_loggers=False: fileConfig()'s default (True) disables every
# logger already registered by that point that isn't named in alembic.ini's
# [loggers] section -- including the app's own "interview_agent" logger whenever
# Alembic runs in the same process as the app (as it does in tests/db/conftest.py).
# Without this, log_event() silently stops emitting for the rest of the process.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# DATABASE_URL comes from the environment/.env only -- never hardcoded in
# alembic.ini or here. This overrides whatever (blank) sqlalchemy.url is in the
# ini file, so `alembic upgrade head` picks up the same DATABASE_URL as the app.
_database_url = load_settings().database_url
if _database_url:
    config.set_main_option("sqlalchemy.url", _database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
