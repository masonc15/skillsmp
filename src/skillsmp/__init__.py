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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import NoReturn

__version__ = "1.2.0"

BASE_URL = "https://skillsmp.com/api/v1/skills"
MCP_URL = "https://skillsmp.com/mcp"
OCCUPATIONS_SITEMAP_URL = "https://skillsmp.com/sitemaps/occupations.xml"
SUBCOMMANDS = ("categories", "occupations", "show")
REQUEST_TIMEOUT = 30
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 4.0
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
DESC_DISPLAY_LIMIT = 200
DESC_PLAIN_LIMIT = 120

# --- TTY / formatting helpers ---


def _isatty(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty()


def _stderr_is_tty() -> bool:
    return _isatty(sys.stderr)


def _style(text: str, code: str, enabled: bool) -> str:
    """Wrap text in an ANSI escape sequence when styling is enabled."""
    return f"\033[{code}m{text}\033[0m" if enabled else text


# --- help texts ---


def _concise_help(color: bool) -> str:
    def bold(t: str) -> str:
        return _style(t, "1", color)

    def lit(t: str) -> str:
        return _style(t, "36", color)

    return f"""\
{bold("skillsmp")} — search the SkillsMP marketplace for agent skills

{bold("Examples:")}
  {lit("skillsmp terraform")}
  {lit('skillsmp --ai "how to optimize database queries"')}

Run "skillsmp --help" for all options.
"""


def _full_help(color: bool) -> str:
    def bold(t: str) -> str:
        return _style(t, "1", color)

    def lit(t: str) -> str:
        return _style(t, "36", color)

    def dim(t: str) -> str:
        return _style(t, "2", color)

    return f"""\
{bold("skillsmp")} — search the SkillsMP marketplace for agent skills

{bold("Usage:")}
  {lit("skillsmp")} {dim("[flags] <query ...>")}
  {lit("skillsmp --ai")} {dim("[flags] <query ...>")}
  {lit("skillsmp show")} {dim("<author>/<skill-name>")}
  {lit("skillsmp categories")}
  {lit("skillsmp occupations")}

{bold("Search modes:")}
  {dim("(default)")}       Keyword search — fast, supports pagination and sorting
  {lit("-a, --ai")}        AI semantic search — natural language, relevance-scored

{bold("Commands:")}
  {lit("show")}            Full details for one skill, including its SKILL.md
  {lit("categories")}      List skill categories by domain
  {lit("occupations")}     List SOC occupation slugs

{bold("Flags:")}
  {lit("-n, --limit")} {dim("N")}   Results per page (1-100, default: 10)
  {lit("-p, --page")} {dim("N")}    Page number (default: 1)
  {lit("-s, --sort")} {dim("KEY")}  Sort order: stars, recent (default: stars)
  {lit("-j, --json")}      Machine-readable JSON output
      {lit("--plain")}     One-line-per-result output for grep/awk
  {lit("-h, --help")}      Show this help
      {lit("--version")}   Show version

  {dim("--limit, --page, and --sort apply to keyword search only.")}

{bold("Examples:")}
  {lit("skillsmp terraform")}
  {lit('skillsmp --ai "how to optimize database queries"')}
  {lit("skillsmp --limit 5 --sort recent react testing")}
  {lit("skillsmp show wshobson/terraform-module-library")}
  {lit("skillsmp --plain react | grep facebook")}

{bold("Environment:")}
  {lit("SKILLSMP_API_KEY")}    API key (required). Read from env or ~/.env.

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


def _mcp_call(tool: str, arguments: dict, *, use_json_errors: bool = False) -> dict:
    """Call a tool on the SkillsMP MCP server (single stateless JSON-RPC POST)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"skillsmp-cli/{__version__}",
        },
    )
    raw = _fetch_with_retry(req, use_json_errors=use_json_errors).decode()

    # The server may answer with plain JSON or a text/event-stream body.
    text = raw.lstrip()
    if not text.startswith("{"):
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:]
                break
    msg = json.loads(text)

    error = msg.get("error")
    result = msg.get("result", {})
    content = result.get("content", [])
    if error or result.get("isError"):
        detail = error.get("message") if error else content[0].get("text", "unknown")
        if use_json_errors:
            json.dump({"error": f"MCP error: {detail}"}, sys.stdout, indent=2)
            print()
        else:
            print(f"skillsmp: MCP error: {detail}", file=sys.stderr)
        raise SystemExit(1)
    return json.loads(content[0]["text"])


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


def _cmd_categories(*, output_json: bool = False, output_plain: bool = False) -> None:
    data = _mcp_call("list_categories", {}, use_json_errors=output_json)
    domains = data.get("domains", [])

    if output_json:
        json.dump(data, sys.stdout, indent=2)
        print()
        return

    if output_plain:
        for domain in domains:
            for cat in domain.get("categories", []):
                parts = [
                    cat.get("slug", ""),
                    cat.get("name", ""),
                    domain.get("domain", ""),
                    str(cat.get("count", 0)),
                ]
                print("\t".join(parts))
        return

    total = sum(len(d.get("categories", [])) for d in domains)
    print(f"{total} categories across {len(domains)} domains\n")
    for domain in domains:
        print(f"{domain.get('domainName', domain.get('domain', 'unknown'))}")
        for cat in domain.get("categories", []):
            count = _format_stars(cat.get("count"))
            print(f"  {cat.get('slug', ''):<32} {count:>7} skills")
        print()


def _cmd_occupations(*, output_json: bool = False, output_plain: bool = False) -> None:
    req = urllib.request.Request(
        OCCUPATIONS_SITEMAP_URL,
        headers={"User-Agent": f"skillsmp-cli/{__version__}"},
    )
    raw = _fetch_with_retry(req, use_json_errors=output_json)

    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    slugs = sorted(
        loc.text.rsplit("/", 1)[1]
        for loc in ET.fromstring(raw).iter(f"{ns}loc")
        if loc.text and "/occupations/" in loc.text
    )

    if output_json:
        json.dump({"total": len(slugs), "occupations": slugs}, sys.stdout, indent=2)
        print()
        return

    if not output_plain:
        print(f"{len(slugs)} occupations\n")
    for slug in slugs:
        print(slug)


def _github_raw_urls(github_url: str) -> list[str]:
    """Candidate raw.githubusercontent.com URLs for a skill's SKILL.md."""
    prefix = "https://github.com/"
    if not github_url.startswith(prefix):
        return []
    rest = github_url[len(prefix):].rstrip("/")
    if "/tree/" in rest:
        repo, tree = rest.split("/tree/", 1)
        return [f"https://raw.githubusercontent.com/{repo}/{tree}/SKILL.md"]
    return [
        f"https://raw.githubusercontent.com/{rest}/{branch}/SKILL.md"
        for branch in ("main", "master")
    ]


def _fetch_skill_md(github_url: str) -> str | None:
    for url in _github_raw_urls(github_url):
        req = urllib.request.Request(
            url, headers={"User-Agent": f"skillsmp-cli/{__version__}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
    return None


def _cmd_show(spec: str, *, output_json: bool = False) -> None:
    author, _, name = spec.partition("/")
    result = _api_request("search", {"q": name, "limit": 50}, use_json_errors=output_json)
    skills = result.get("data", {}).get("skills", [])
    matches = [
        s
        for s in skills
        if s.get("author", "").lower() == author.lower()
        and s.get("name", "").lower() == name.lower()
    ]

    if not matches:
        if output_json:
            json.dump({"error": f"skill not found: {spec}"}, sys.stdout, indent=2)
            print()
        else:
            print(f"skillsmp: skill not found: {spec}", file=sys.stderr)
        raise SystemExit(1)

    skill = matches[0]
    d = _normalize_skill(skill)
    skill_md = _fetch_skill_md(d["githubUrl"])

    if output_json:
        d["skillMd"] = skill_md
        json.dump(d, sys.stdout, indent=2)
        print()
        return

    stars = _format_stars(d["stars"])
    updated = _format_timestamp(d["updatedAt"])
    print(f"{d['author']}/{d['name']}  [{stars} stars, updated {updated}]\n")
    if d["description"]:
        print(f"{d['description']}\n")
    if d["githubUrl"]:
        print(f"github:   {d['githubUrl']}")
    if d["skillUrl"]:
        print(f"skillsmp: {d['skillUrl']}")
    if len(matches) > 1:
        print(
            f"skillsmp: {len(matches) - 1} more skills matched {spec}, showing the top result",
            file=sys.stderr,
        )
    if skill_md is not None:
        print(f"\n--- SKILL.md ---\n{skill_md}")
    else:
        print("skillsmp: could not fetch SKILL.md from GitHub", file=sys.stderr)


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
    command = "search"
    ai = False
    limit: int | None = None
    page: int | None = None
    sort: str | None = None
    output_json = False
    output_plain = False
    query_parts: list[str] = []

    if argv and argv[0] in SUBCOMMANDS:
        command = argv[0]
        argv = argv[1:]

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(_full_help(_isatty(sys.stdout)))
            raise SystemExit(0)
        elif arg == "--version":
            print(f"skillsmp {__version__}")
            raise SystemExit(0)
        elif arg in ("-a", "--ai"):
            ai = True
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
        elif command == "search":
            # Everything from the first positional on is the query.
            query_parts.extend(argv[i:])
            break
        else:
            query_parts.append(arg)
        i += 1

    # No args at all: concise help.
    if command == "search" and not query_parts:
        print(_concise_help(_stderr_is_tty()), file=sys.stderr)
        raise SystemExit(2)

    # Validate.
    if output_json and output_plain:
        _die("--json and --plain are mutually exclusive")

    if limit is not None and not 1 <= limit <= 100:
        _die(f"--limit must be 1-100 (got: {limit})")

    if sort is not None and sort not in ("stars", "recent"):
        _die(f"--sort must be 'stars' or 'recent' (got: {sort})")

    mode = command if command != "search" else ("ai" if ai else "search")

    if mode != "search" and any(x is not None for x in (limit, page, sort)):
        target = "--ai search" if mode == "ai" else f"the {mode} command"
        _die(f"--limit, --page, --sort do not apply to {target}")

    if command in SUBCOMMANDS and ai:
        _die(f"--ai does not apply to the {command} command")

    if command in ("categories", "occupations") and query_parts:
        _die(f"the {command} command takes no arguments")

    if command == "show":
        spec = query_parts[0] if query_parts else ""
        author, _, name = spec.partition("/")
        if len(query_parts) != 1 or not author or not name:
            _die("show requires a <author>/<skill-name> argument")
        if output_plain:
            _die("--plain does not apply to the show command")

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
    mode = args["mode"]

    if mode == "categories":
        _cmd_categories(output_json=args["json"], output_plain=args["plain"])
    elif mode == "occupations":
        _cmd_occupations(output_json=args["json"], output_plain=args["plain"])
    elif mode == "show":
        _cmd_show(args["query"], output_json=args["json"])
    elif mode == "ai":
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
