"""Search the SkillsMP marketplace for agent skills."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

__version__ = "1.0.0"

# --- TTY / formatting helpers ---


def _stderr_is_tty() -> bool:
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _bold(text: str) -> str:
    """Wrap text in bold escapes when stderr is a TTY."""
    if _stderr_is_tty():
        return f"\033[1m{text}\033[0m"
    return text


# --- help texts ---

CONCISE_HELP = f"""\
{_bold("skillsmp")} — search the SkillsMP marketplace for agent skills

{_bold("Examples:")}
  skillsmp terraform
  skillsmp --ai "how to optimize database queries"

Run "skillsmp --help" for all options.
"""

FULL_HELP = f"""\
{_bold("skillsmp")} — search the SkillsMP marketplace for agent skills

{_bold("Usage:")}
  skillsmp [flags] <query ...>
  skillsmp --ai [flags] <query ...>

{_bold("Search modes:")}
  (default)       Keyword search — fast, supports pagination and sorting
  -a, --ai        AI semantic search — natural language, relevance-scored

{_bold("Flags:")}
  -n, --limit N   Results per page (1-100, default: 10)
  -p, --page N    Page number (default: 1)
  -s, --sort KEY  Sort order: stars, recent (default: stars)
  -j, --json      Machine-readable JSON output
      --plain     One-line-per-result output for grep/awk
  -h, --help      Show this help
      --version   Show version

  --limit, --page, and --sort apply to keyword search only.

{_bold("Examples:")}
  skillsmp terraform
  skillsmp --ai "how to optimize database queries"
  skillsmp --limit 5 --sort recent react testing
  skillsmp --json deployment
  skillsmp --plain react | grep facebook

{_bold("Environment:")}
  SKILLSMP_API_KEY    API key (required). Read from env or ~/.env.

Docs: https://skillsmp.com
"""

BASE_URL = "https://skillsmp.com/api/v1/skills"
REQUEST_TIMEOUT = 30
DESC_DISPLAY_LIMIT = 200
DESC_PLAIN_LIMIT = 120

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


def _api_request(
    endpoint: str, params: dict, *, use_json_errors: bool = False
) -> dict:
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
        if use_json_errors:
            json.dump({"error": msg, "code": e.code}, sys.stdout, indent=2)
            print()
        else:
            print(f"skillsmp: API error ({e.code}): {msg}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as e:
        if use_json_errors:
            json.dump({"error": str(e.reason)}, sys.stdout, indent=2)
            print()
        else:
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


def _print_skill_plain(skill: dict, score: float | None = None) -> None:
    parts = [
        f"{skill.get('author', 'unknown')}/{skill.get('name', 'unknown')}",
        str(skill.get("stars", 0)),
        skill.get("description", "")[:DESC_PLAIN_LIMIT],
        skill.get("githubUrl", ""),
    ]
    if score is not None:
        parts.append(str(round(score, 4)))
    print("\t".join(parts))


# --- commands ---


def _cmd_search(
    query: str,
    *,
    limit: int = 10,
    page: int = 1,
    sort: str = "stars",
    output_json: bool = False,
    output_plain: bool = False,
) -> None:
    params = {"q": query, "limit": limit, "page": page, "sortBy": sort}
    result = _api_request("search", params, use_json_errors=output_json)
    data = result.get("data", {})
    skills = data.get("skills", [])
    pagination = data.get("pagination", {})

    if output_json:
        json.dump(
            {
                "query": query,
                "mode": "keyword",
                "total": pagination.get("total", 0),
                "page": pagination.get("page", 1),
                "totalPages": pagination.get("totalPages", 1),
                "skills": [_normalize_skill(s) for s in skills],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return

    if output_plain:
        for s in skills:
            _print_skill_plain(s)
        return

    total = pagination.get("total", 0)
    pg = pagination.get("page", 1)
    total_pages = pagination.get("totalPages", 1)

    print(f'Keyword search: "{query}" — {total} results (page {pg}/{total_pages})\n')
    if not skills:
        print("  No results found.")
        return
    for s in skills:
        _print_skill(s)


def _cmd_ai_search(
    query: str,
    *,
    output_json: bool = False,
    output_plain: bool = False,
) -> None:
    params = {"q": query}
    result = _api_request("ai-search", params, use_json_errors=output_json)
    data = result.get("data", {})
    entries = data.get("data", [])

    with_skill = [e for e in entries if e.get("skill")]
    without_skill = [e for e in entries if not e.get("skill")]

    if output_json:
        json.dump(
            {
                "query": query,
                "mode": "semantic",
                "total": len(entries),
                "withMetadata": len(with_skill),
                "skills": [
                    _normalize_skill(e["skill"], score=e.get("score"))
                    for e in with_skill
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return

    if output_plain:
        for entry in with_skill:
            _print_skill_plain(entry["skill"], score=entry.get("score"))
        return

    print(
        f'AI search: "{query}" — {len(entries)} results '
        f"({len(with_skill)} with metadata)\n"
    )
    if not entries:
        print("  No results found.")
        return
    for entry in with_skill:
        _print_skill(entry["skill"], score=entry.get("score"))
    if without_skill:
        print(
            f"  ({len(without_skill)} additional results without full metadata, skipped)"
        )


# --- error handling ---


def _die(msg: str) -> None:
    print(f"skillsmp: {msg}", file=sys.stderr)
    print('Try "skillsmp --help" for usage.', file=sys.stderr)
    raise SystemExit(2)


# --- argument parsing ---


def _parse_args(argv: list[str]) -> dict:
    mode = "search"
    limit: int | None = None
    page: int | None = None
    sort: str | None = None
    output_json = False
    output_plain = False
    query_parts: list[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(FULL_HELP)
            raise SystemExit(0)
        elif arg == "--version":
            print(f"skillsmp {__version__}")
            raise SystemExit(0)
        elif arg in ("-a", "--ai"):
            mode = "ai"
        elif arg in ("-j", "--json"):
            output_json = True
        elif arg == "--plain":
            output_plain = True
        elif arg in ("-n", "--limit"):
            i += 1
            if i >= len(argv):
                _die(f"flag {arg} requires a value")
            limit = argv[i]  # type: ignore[assignment]
        elif arg in ("-s", "--sort"):
            i += 1
            if i >= len(argv):
                _die(f"flag {arg} requires a value")
            sort = argv[i]
        elif arg in ("-p", "--page"):
            i += 1
            if i >= len(argv):
                _die(f"flag {arg} requires a value")
            page = argv[i]  # type: ignore[assignment]
        elif arg == "--":
            i += 1
            query_parts.extend(argv[i:])
            break
        elif arg.startswith("-"):
            _die(f"unknown flag: {arg}")
        else:
            query_parts.extend(argv[i:])
            break
        i += 1

    # No args: concise help, not a bare error.
    if not query_parts:
        print(CONCISE_HELP, file=sys.stderr)
        raise SystemExit(2)

    # Validate.
    if output_json and output_plain:
        _die("--json and --plain are mutually exclusive")
    if limit is not None:
        try:
            limit = int(limit)
        except ValueError:
            _die(f"--limit must be a number (got: {limit})")
        if not 1 <= limit <= 100:
            _die(f"--limit must be 1-100 (got: {limit})")

    if page is not None:
        try:
            page = int(page)
        except ValueError:
            _die(f"--page must be a number (got: {page})")

    if sort is not None and sort not in ("stars", "recent"):
        _die(f"--sort must be 'stars' or 'recent' (got: {sort})")

    if mode == "ai" and any(x is not None for x in (limit, page, sort)):
        _die("--limit, --page, --sort do not apply to --ai search")

    return {
        "mode": mode,
        "query": " ".join(query_parts),
        "limit": limit if limit is not None else 10,
        "page": page if page is not None else 1,
        "sort": sort if sort is not None else "stars",
        "json": output_json,
        "plain": output_plain,
    }


# --- entry point ---


def main() -> None:
    args = _parse_args(sys.argv[1:])

    if args["mode"] == "ai":
        _cmd_ai_search(
            args["query"],
            output_json=args["json"],
            output_plain=args["plain"],
        )
    else:
        _cmd_search(
            args["query"],
            limit=args["limit"],
            page=args["page"],
            sort=args["sort"],
            output_json=args["json"],
            output_plain=args["plain"],
        )
