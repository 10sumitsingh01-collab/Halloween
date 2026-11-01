import pytest
from datetime import datetime, timezone
from typing import Callable
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import os
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["NOTIFIER_CHANNEL"] = "mock"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from app.config import settings
settings.SCHEDULER_ENABLED = False
settings.NOTIFIER_CHANNEL = "mock"
settings.TELEGRAM_BOT_TOKEN = None

from app.db import Base, get_db
from app.main import app as fastapi_app
from app.services.clock import get_clock
import app.models  # load all models


@pytest.fixture(scope="function")
def db_session():
    """In-memory SQLite database session for isolated testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient wired to the test in-memory database."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def caregiver_headers():
    return {"X-API-Key": settings.CAREGIVER_API_KEY}


@pytest.fixture
def patient_headers():
    return {"X-API-Key": settings.PATIENT_API_KEY}


@pytest.fixture(scope="function")
def make_client(db_session):
    """
    Factory fixture: make_client(fixed_now) -> TestClient with frozen clock.

    Usage in tests:
        def test_something(make_client, db_session, ...):
            fixed = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
            client = make_client(fixed)
            ...

    The returned TestClient does NOT use a context manager; call it directly.
    Dependency overrides are cleared after the test via the fixture teardown.
    """
    created_clients = []

    def _factory(fixed_now: datetime) -> TestClient:
        def override_get_db():
            try:
                yield db_session
            finally:
                pass

        fixed_clock: Callable[[], datetime] = lambda: fixed_now

        def override_get_clock() -> Callable[[], datetime]:
            return fixed_clock

        fastapi_app.dependency_overrides[get_db] = override_get_db
        fastapi_app.dependency_overrides[get_clock] = override_get_clock
        tc = TestClient(fastapi_app, raise_server_exceptions=False)
        created_clients.append(tc)
        return tc

    yield _factory

    fastapi_app.dependency_overrides.clear()
