"""Alembic environment: connects as the migration owner named by the application settings.

``property_focus.adapters.db.migrate`` passes its ``Settings`` in ``config.attributes``; a
plain ``alembic`` command reads them from the ``PF_*`` environment instead.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from property_focus.adapters.db import database_url
from property_focus.adapters.db.models import Base
from property_focus.config import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if context.is_offline_mode():
    raise SystemExit("offline (--sql) migrations are not supported; run against a database")

settings: Settings = config.attributes.get("settings") or Settings.from_environment()
url = asyncio.run(database_url(settings, "migration"))

engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
try:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
