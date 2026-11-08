import pytest

from controller.errors import ConfigurationError
from controller.storage.store import MAX_EVENT_INSERT_BATCH_ROWS, DatabaseStore


@pytest.mark.anyio
async def test_database_url_is_read_from_environment_without_echoing_it():
    store = DatabaseStore.from_environment(
        {
            "CONTROLLER_DATABASE_URL": "postgresql+asyncpg://user:unit-only-placeholder@db.test/app"
        }
    )
    assert store.engine.url.drivername == "postgresql+asyncpg"
    assert store.engine.url.password is not None
    await store.close()


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"CONTROLLER_DATABASE_URL": "not a url"},
        {
            "CONTROLLER_DATABASE_URL": "postgresql://user:unit-only-placeholder@db.test/app"
        },
        {"CONTROLLER_DATABASE_URL": "postgresql+asyncpg:///app"},
    ],
)
def test_missing_or_invalid_database_configuration_is_actionable_and_safe(values):
    with pytest.raises(ConfigurationError) as exc_info:
        DatabaseStore.from_environment(values)
    assert "unit-only-placeholder" not in str(exc_info.value)


@pytest.mark.anyio
async def test_store_close_is_idempotent_and_disallows_reuse():
    store = DatabaseStore("postgresql+asyncpg://user:unit-only-placeholder@db.test/app")
    await store.close()
    await store.close()
    with pytest.raises(RuntimeError, match="closed"):
        await store.initialize()


@pytest.mark.anyio
async def test_stream_batch_rejects_parameter_limit_before_database_access():
    store = DatabaseStore("postgresql+asyncpg://user:unit-only-placeholder@db.test/app")

    async def no_events():
        if False:
            yield None

    try:
        with pytest.raises(ValueError, match="row limit"):
            await store.commit_chunk_stream(
                "unit", 1, no_events(), batch_size=MAX_EVENT_INSERT_BATCH_ROWS + 1
            )
    finally:
        await store.close()
