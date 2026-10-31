from __future__ import annotations

import io
import json
import os
import urllib.error
from unittest import mock

import pytest

import skillsmp

FAKE_API_KEY = "sk-test-1234567890"


def make_raw_response(body: bytes) -> mock.MagicMock:
    """Fake urlopen response (context manager with .read())."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = body
    return response


def make_response(data: dict) -> mock.MagicMock:
    """Fake urlopen response with a JSON body."""
    return make_raw_response(json.dumps(data).encode())


def make_http_error(code: int, reason: str = "Error", body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, reason, {}, io.BytesIO(body))


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Isolate process env per test and provide a default API key.

    There's no GitHub token unless a test sets one, and the skill-path
    cache starts empty in a temporary directory.
    """
    monkeypatch.setattr(skillsmp.os, "environ", dict(os.environ))
    monkeypatch.setenv("SKILLSMP_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(skillsmp, "_github_token", lambda: None)
    monkeypatch.setattr(skillsmp, "_skill_paths", skillsmp._SkillPathCache())
    monkeypatch.setattr(skillsmp, "_repo_trees", {})


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
def mock_urlopen():
    def _mock_urlopen(response_data: dict):
        return mock.patch(
            "skillsmp.urllib.request.urlopen", return_value=make_response(response_data)
        )

    return _mock_urlopen
