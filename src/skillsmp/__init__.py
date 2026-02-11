"""Search the SkillsMP marketplace for agent skills."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

__version__ = "0.1.0"

BASE_URL = "https://skillsmp.com/api/v1/skills"
REQUEST_TIMEOUT = 30
DESC_DISPLAY_LIMIT = 200

# --- API key ---


def _load_env_file() -> None:
    """Source key=value pairs from ~/.env if the file exists."""
    env_path = os.path.join(os.path.expanduser("~"), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = val


def _get_api_key() -> str:
    key = os.environ.get("SKILLSMP_API_KEY", "")
    if not key:
        _load_env_file()
        key = os.environ.get("SKILLSMP_API_KEY", "")
    if not key:
        print(
            "skillsmp: SKILLSMP_API_KEY not set. Export it or add to ~/.env.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return key


# --- API client ---


def _api_request(endpoint: str, params: dict) -> dict:
    api_key = _get_api_key()
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE_URL}/{endpoint}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"skillsmp-cli/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body: dict = {}
        try:
            body = json.loads(e.read())
        except Exception:
            pass
        err = body.get("error", {})
        msg = err.get("message", e.reason)
        print(f"skillsmp: API error ({e.code}): {msg}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as e:
        print(f"skillsmp: network error: {e.reason}", file=sys.stderr)
        raise SystemExit(1)


# --- formatting ---


def _format_timestamp(ts: int | None) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _format_stars(n: int | None) -> str:
    if n is None:
        return "0"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _normalize_skill(skill: dict, score: float | None = None) -> dict:
    d = {
        "name": skill.get("name", "unknown"),
        "author": skill.get("author", "unknown"),
        "description": skill.get("description", ""),
        "stars": skill.get("stars", 0),
        "updatedAt": skill.get("updatedAt"),
        "githubUrl": skill.get("githubUrl", ""),
        "skillUrl": skill.get("skillUrl", ""),
    }
    if score is not None:
        d["relevanceScore"] = round(score, 4)
    return d


def _print_skill(skill: dict, score: float | None = None) -> None:
    name = skill.get("name", "unknown")
    author = skill.get("author", "unknown")
    stars = _format_stars(skill.get("stars"))
    updated = _format_timestamp(skill.get("updatedAt"))
    desc = skill.get("description", "")
    github = skill.get("githubUrl", "")
    skillsmp_url = skill.get("skillUrl", "")

    header = f"  {author}/{name}"
    if score is not None:
        header += f"  (relevance: {score:.2f})"
    header += f"  [{stars} stars, updated {updated}]"
    print(header)
    if desc:
        print(f"    {desc[:DESC_DISPLAY_LIMIT]}")
    if github:
        print(f"    github: {github}")
    if skillsmp_url:
        print(f"    skillsmp: {skillsmp_url}")
    print()


# --- entry point ---


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: skillsmp <query>", file=sys.stderr)
        raise SystemExit(2)

    query = " ".join(sys.argv[1:])
    params = {"q": query, "limit": 10, "page": 1, "sortBy": "stars"}
    result = _api_request("search", params)
    data = result.get("data", {})
    skills = data.get("skills", [])
    pagination = data.get("pagination", {})

    total = pagination.get("total", 0)
    page = pagination.get("page", 1)
    total_pages = pagination.get("totalPages", 1)

    print(f'Keyword search: "{query}" — {total} results (page {page}/{total_pages})\n')
    if not skills:
        print("  No results found.")
        return
    for s in skills:
        _print_skill(s)
