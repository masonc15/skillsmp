"""Search the SkillsMP marketplace for agent skills."""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import NoReturn

__version__ = "1.0.0"

BASE_URL = "https://skillsmp.com/api/v1/skills"
REQUEST_TIMEOUT = 30
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 4.0
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
DESC_DISPLAY_LIMIT = 200
DESC_PLAIN_LIMIT = 120

# --- TTY / formatting helpers ---


def _stderr_is_tty() -> bool:
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _bold(text: str) -> str:
    """Wrap text in bold escapes when stderr is a TTY."""
    if _stderr_is_tty():
        return f"\033[1m{text}\033[0m"
    return text


# --- help texts ---


def _concise_help() -> str:
    return f"""\
{_bold("skillsmp")} — search the SkillsMP marketplace for agent skills

{_bold("Examples:")}
  skillsmp terraform
  skillsmp --ai "how to optimize database queries"

Run "skillsmp --help" for all options.
"""


def _full_help() -> str:
    return f"""\
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

Docs: https://github.com/masonc15/skillsmp
"""

# --- error handling ---


def _die(msg: str) -> NoReturn:
    print(f"skillsmp: {msg}", file=sys.stderr)
    print('Try "skillsmp --help" for usage.', file=sys.stderr)
    raise SystemExit(2)


# --- API key ---


def _read_dotenv_key(name: str) -> str | None:
    """Read a single variable from ~/.env (handles `export VAR=val` and quotes)."""
    env_path = os.path.join(os.path.expanduser("~"), ".env")
    if not os.path.isfile(env_path):
        return None

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            key, _, val = line.removeprefix("export ").partition("=")
            if key.strip() == name:
                return val.strip().strip("\"'")
    return None


def _get_api_key() -> str:
    key = os.environ.get("SKILLSMP_API_KEY") or _read_dotenv_key("SKILLSMP_API_KEY")
    if not key:
        _die("SKILLSMP_API_KEY not set. Export it or add to ~/.env.")
    return key


# --- API client ---


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, urllib.error.URLError)


def _retry_delay(attempt: int) -> float:
    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
    return delay * random.uniform(1.0, 1.3)


def _fetch_with_retry(
    req: urllib.request.Request, *, use_json_errors: bool = False
) -> bytes:
    """GET/POST with retries; on failure, report the error and exit 1."""
    last_exc: Exception | None = None
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last_exc = e
            is_last = attempt == RETRY_MAX_ATTEMPTS - 1
            if not is_last and _is_retryable(e):
                retries_left = RETRY_MAX_ATTEMPTS - attempt - 1
                if isinstance(e, urllib.error.HTTPError):
                    label = f"server error ({e.code})"
                else:
                    label = f"network error: {e.reason}"
                print(
                    f"skillsmp: {label}, retrying ({retries_left} left)...",
                    file=sys.stderr,
                )
                time.sleep(_retry_delay(attempt))
                continue
            break

    # All retries exhausted or non-retryable error — report and exit.
    if isinstance(last_exc, urllib.error.HTTPError):
        body: dict = {}
        try:
            body = json.loads(last_exc.read())
        except Exception:
            pass
        msg = body.get("error", {}).get("message", last_exc.reason)
        payload = {"error": msg, "code": last_exc.code}
        human = f"API error ({last_exc.code}): {msg}"
    else:
        payload = {"error": str(last_exc.reason)}
        human = f"network error: {last_exc.reason}"

    if use_json_errors:
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        print(f"skillsmp: {human}", file=sys.stderr)
    raise SystemExit(1)


def _api_request(
    endpoint: str, params: dict, *, use_json_errors: bool = False
) -> dict:
    api_key = _get_api_key()
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(
        f"{BASE_URL}/{endpoint}?{qs}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"skillsmp-cli/{__version__}",
        },
    )
    return json.loads(_fetch_with_retry(req, use_json_errors=use_json_errors))


# --- formatting ---


def _format_timestamp(ts: int | str | None) -> str:
    if not ts:
        return "unknown"
    if isinstance(ts, str):
        try:
            ts = int(ts)
        except ValueError:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except ValueError:
                return ts[:10]
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
    d = _normalize_skill(skill, score)

    header = f"  {d['author']}/{d['name']}"
    if score is not None:
        header += f"  (relevance: {score:.2f})"
    header += f"  [{_format_stars(d['stars'])} stars, updated {_format_timestamp(d['updatedAt'])}]"
    print(header)
    if d["description"]:
        print(f"    {d['description'][:DESC_DISPLAY_LIMIT]}")
    if d["githubUrl"]:
        print(f"    github: {d['githubUrl']}")
    if d["skillUrl"]:
        print(f"    skillsmp: {d['skillUrl']}")
    print()


def _print_skill_plain(skill: dict, score: float | None = None) -> None:
    d = _normalize_skill(skill, score)
    parts = [
        f"{d['author']}/{d['name']}",
        str(d["stars"]),
        d["description"][:DESC_PLAIN_LIMIT],
        d["githubUrl"],
    ]
    if score is not None:
        parts.append(str(d["relevanceScore"]))
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
        if _stderr_is_tty():
            print(
                f'\n  Tip: try "skillsmp --ai {query}" for semantic search.',
                file=sys.stderr,
            )
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
    skipped = len(entries) - len(with_skill)
    if skipped:
        print(f"  ({skipped} additional results without full metadata, skipped)")


# --- argument parsing ---


def _take_value(argv: list[str], i: int, flag: str) -> tuple[str, int]:
    """Consume the next argv element as the value of `flag`."""
    i += 1
    if i >= len(argv):
        _die(f"flag {flag} requires a value")
    return argv[i], i


def _parse_int_flag(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        _die(f"{name} must be a number (got: {raw})")


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
            print(_full_help())
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
            raw, i = _take_value(argv, i, arg)
            limit = _parse_int_flag("--limit", raw)
        elif arg in ("-s", "--sort"):
            sort, i = _take_value(argv, i, arg)
        elif arg in ("-p", "--page"):
            raw, i = _take_value(argv, i, arg)
            page = _parse_int_flag("--page", raw)
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

    # No args at all: concise help.
    if not query_parts:
        print(_concise_help(), file=sys.stderr)
        raise SystemExit(2)

    # Validate.
    if output_json and output_plain:
        _die("--json and --plain are mutually exclusive")

    if limit is not None and not 1 <= limit <= 100:
        _die(f"--limit must be 1-100 (got: {limit})")

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
        # Progress indicator (TTY only, human output only).
        if _stderr_is_tty() and not args["json"] and not args["plain"]:
            print("Searching (AI)...\r", end="", file=sys.stderr, flush=True)
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
