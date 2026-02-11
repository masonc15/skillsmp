from __future__ import annotations

import json
import os
from unittest import mock

import pytest

import skillsmp

FAKE_API_KEY = "sk-test-1234567890"


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate process env per test and provide a default API key."""
    monkeypatch.setattr(skillsmp.os, "environ", dict(os.environ))
    monkeypatch.setenv("SKILLSMP_API_KEY", FAKE_API_KEY)


@pytest.fixture
def make_skill():
    def _make_skill(**overrides):
        base = {
            "name": "terraform-deploy",
            "author": "acme",
            "description": "Deploy infrastructure with Terraform",
            "stars": 42,
            "updatedAt": 1700000000,
            "githubUrl": "https://github.com/acme/terraform-deploy",
            "skillUrl": "https://skillsmp.com/skills/terraform-deploy",
        }
        base.update(overrides)
        return base

    return _make_skill


@pytest.fixture
def make_keyword_response(make_skill):
    def _make_keyword_response(skills=None, total=1, page=1, total_pages=1):
        if skills is None:
            skills = [make_skill()]
        return {
            "data": {
                "skills": skills,
                "pagination": {
                    "total": total,
                    "page": page,
                    "totalPages": total_pages,
                },
            }
        }

    return _make_keyword_response


@pytest.fixture
def make_ai_response(make_skill):
    def _make_ai_response(entries=None):
        if entries is None:
            entries = [{"skill": make_skill(), "score": 0.95}]
        return {"data": {"data": entries}}

    return _make_ai_response


@pytest.fixture
def mock_urlopen():
    def _mock_urlopen(response_data: dict):
        body = json.dumps(response_data).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        return mock.patch("skillsmp.urllib.request.urlopen", return_value=response)

    return _mock_urlopen
