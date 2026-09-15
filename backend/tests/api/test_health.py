"""Smoke tests for health and root endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    def test_health_check_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_check_body(self, client):
        response = client.get("/health")
        assert response.json() == {"status": "healthy"}

    def test_root_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_root_has_message(self, client):
        data = client.get("/").json()
        assert "message" in data

    def test_root_has_version(self, client):
        data = client.get("/").json()
        assert "version" in data

    def test_openapi_docs_accessible(self, client):
        response = client.get("/docs")
        assert response.status_code == 200


class TestRuntimeConfigurationWarnings:
    def test_warns_when_local_backend_uses_docker_database_host(self, monkeypatch, caplog):
        from app import main

        monkeypatch.setenv("TUBELESS_RUNTIME", "local")
        monkeypatch.setattr(
            main.settings,
            "database_url",
            "postgresql+asyncpg://postgres:postgres@postgres:5432/tubeless",
        )

        with caplog.at_level("WARNING", logger="app.main"):
            main._warn_on_runtime_mismatch()

        assert "local backend is using Docker database host" in caplog.text
