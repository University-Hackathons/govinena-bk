"""Migration configuration shares application settings and metadata."""

from alembic import context

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import build_engine
from app.models import Base

settings = Settings()
configure_logging(settings.log_level)
target_metadata = Base.metadata
engine = build_engine(settings)

try:
    if context.is_offline_mode():
        context.configure(
            url=engine.url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=engine.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
finally:
    engine.dispose()
