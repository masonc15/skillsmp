"""Search skills.sh and the SkillsMP marketplace for agent skills."""

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
from typing import Callable, NoReturn

__version__ = "1.3.0"

BASE_URL = "https://skillsmp.com/api/v1/skills"
MCP_URL = "https://skillsmp.com/mcp"
MCP_PROTOCOL_VERSION = "2025-06-18"
OCCUPATIONS_SITEMAP_URL = "https://skillsmp.com/sitemaps/occupations.xml"
SUBCOMMANDS = ("categories", "occupations", "show")
FILTER_FLAGS = ("--category", "--occupation", "--language")
REQUEST_TIMEOUT = 30
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 4.0
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
MAX_LIMIT = 50
# --exclude filters client-side, so it may need several API pages to fill one
# page of results. Cap the requests so a broad exclusion can't loop for long.
EXCLUDE_MAX_REQUESTS = 5
DESC_DISPLAY_LIMIT = 200
SKILLSSH_URL = "https://skills.sh"
# skills.sh search returns at most this many hits and has no paging.
SKILLSSH_MAX_RESULTS = 200
SOURCES = ("skills.sh", "skillsmp")
SOURCE_LABELS = {"skills.sh": "skills.sh", "skillsmp": "SkillsMP"}
SORTS = {"skills.sh": ("relevance", "installs"), "skillsmp": ("stars", "recent")}
DEFAULT_SORT = {source: sorts[0] for source, sorts in SORTS.items()}
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
{bold("skillsmp")} — search skills.sh and SkillsMP for agent skills

{bold("Examples:")}
  {lit("skillsmp terraform")}
  {lit("skillsmp --limit 5 --sort recent react testing")}

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
{bold("skillsmp")} — search skills.sh and SkillsMP for agent skills

{bold("Usage:")}
  {lit("skillsmp")} {dim("[flags] <query ...>")}
  {lit("skillsmp -i")} {dim("[flags] [query ...]")}
  {lit("skillsmp show")} {dim("<owner>/<repo>/<skill> | <author>/<skill>")}
  {lit("skillsmp categories")}
  {lit("skillsmp occupations")}

{bold("Commands:")}
  {lit("show")}            Full details for one skill, including its SKILL.md
  {lit("categories")}      List category slugs for --category
  {lit("occupations")}     List occupation slugs for --occupation

{bold("Flags:")}
  {lit("-i, --interactive")}   Browse results in a full-screen TUI
  {lit("-n, --limit")} {dim("N")}       Results per page (1-50, default: 10, or 20 with -i)
  {lit("-p, --page")} {dim("N")}        Page number (default: 1)
  {lit("-s, --sort")} {dim("KEY")}      skills.sh: relevance (default), installs
                      SkillsMP: stars (default), recent
      {lit("--source")} {dim("S")}      skills.sh or skillsmp (default: skills.sh, falling
                      back to SkillsMP when it has no results or fails)
      {lit("--category")} {dim("C")}    Filter by category slug (see: skillsmp categories)
      {lit("--occupation")} {dim("O")}  Filter by occupation slug (see: skillsmp occupations)
      {lit("--language")} {dim("L")}    Filter by content language code (en, zh, ja, ...)
  {lit("-x, --exclude")} {dim("T")}     Drop results whose author/name or description contains T
                      (case-insensitive, repeatable)
  {lit("-j, --json")}          Machine-readable JSON output
      {lit("--plain")}         One-line-per-result output for grep/awk
  {lit("-h, --help")}          Show this help
      {lit("--version")}       Show version

  {dim("--limit, --page, --sort, --exclude, and the filters apply to search only.")}
  {dim("The filters and the stars/recent sorts are SkillsMP's, so they search it directly.")}

{bold("Examples:")}
  {lit("skillsmp terraform")}
  {lit("skillsmp -i react")}
  {lit("skillsmp --sort installs react testing")}
  {lit("skillsmp --category cicd --json deployment")}
  {lit("skillsmp --occupation lawyers --language en contracts")}
  {lit("skillsmp -x native -x affaan-m react")}
  {lit("skillsmp show hashicorp/agent-skills/terraform-test")}
  {lit("skillsmp show --source skillsmp wshobson/terraform-module-library")}
  {lit("skillsmp --plain react | grep facebook")}

{bold("Environment:")}
  {lit("SKILLSMP_API_KEY")}    SkillsMP API key (optional, raises its daily search quota).
                      Read from env or ~/.env. Never sent to skills.sh.

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


def _get_api_key() -> str | None:
    return os.environ.get("SKILLSMP_API_KEY") or _read_dotenv_key("SKILLSMP_API_KEY")


# --- API client ---


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, urllib.error.URLError)


def _retry_delay(attempt: int) -> float:
    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
    return delay * random.uniform(1.0, 1.3)


class FetchError(Exception):
    """A request failed for good. `payload` is the --json error object."""

    def __init__(self, message: str, payload: dict) -> None:
        super().__init__(message)
        self.payload = payload
        # The API's error code, e.g. DAILY_QUOTA_EXCEEDED.
        self.code: str | None = payload.get("errorCode")


QUOTA_ERROR_CODES = {"DAILY_QUOTA_EXCEEDED", "MONTHLY_QUOTA_EXCEEDED"}


def _format_wait(seconds: int) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes = -(-rem // 60) if hours == 0 else rem // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _fetch(
    req: urllib.request.Request,
    *,
    on_retry: Callable[[str], None] | None = None,
    on_headers: Callable[[dict], None] | None = None,
) -> bytes:
    """GET/POST with retries; raise FetchError once they run out.

    `on_headers` gets each response's headers, error responses included, so
    callers can track the X-RateLimit-* quota.
    """
    last_exc: Exception | None = None
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                if on_headers:
                    on_headers(dict(resp.headers.items()))
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last_exc = e
            if on_headers and isinstance(e, urllib.error.HTTPError) and e.headers:
                on_headers(dict(e.headers.items()))
            is_last = attempt == RETRY_MAX_ATTEMPTS - 1
            if not is_last and _is_retryable(e):
                retries_left = RETRY_MAX_ATTEMPTS - attempt - 1
                if isinstance(e, urllib.error.HTTPError):
                    label = f"server error ({e.code})"
                else:
                    label = f"network error: {e.reason}"
                if on_retry:
                    on_retry(f"{label}, retrying ({retries_left} left)...")
                time.sleep(_retry_delay(attempt))
                continue
            break

    if isinstance(last_exc, urllib.error.HTTPError):
        body: dict = {}
        try:
            body = json.loads(last_exc.read())
        except Exception:
            pass
        error = body.get("error", {})
        if isinstance(error, str):
            error = {"message": body.get("message") or error}
        elif not isinstance(error, dict):
            error = {}
        msg = error.get("message", last_exc.reason)
        payload = {"error": msg, "code": last_exc.code}
        human = f"API error ({last_exc.code}): {msg}"
        if error.get("code"):
            payload["errorCode"] = error["code"]
        if error.get("resetAt"):
            payload["resetAt"] = error["resetAt"]
        if isinstance(error.get("retryAfter"), int):
            payload["retryAfter"] = error["retryAfter"]
            if error.get("code") in QUOTA_ERROR_CODES:
                human = (
                    f"SkillsMP search quota used up ({msg.rstrip('.')}); "
                    f"it resets in {_format_wait(error['retryAfter'])}"
                )
            else:
                human += f" Try again in {_format_wait(error['retryAfter'])}."
        raise FetchError(human, payload)
    raise FetchError(
        f"network error: {last_exc.reason}", {"error": str(last_exc.reason)}
    )


def _fetch_with_retry(
    req: urllib.request.Request, *, use_json_errors: bool = False
) -> bytes:
    """Like _fetch, but report a failure and exit 1."""

    def warn(msg: str) -> None:
        print(f"skillsmp: {msg}", file=sys.stderr)

    try:
        return _fetch(req, on_retry=warn)
    except FetchError as e:
        if use_json_errors:
            json.dump(e.payload, sys.stdout, indent=2)
            print()
        else:
            warn(str(e))
        raise SystemExit(1)


def _api_url_request(endpoint: str, params: dict) -> urllib.request.Request:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"User-Agent": f"skillsmp-cli/{__version__}"}
    # Anonymous requests work too, but with much lower rate limits.
    api_key = _get_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return urllib.request.Request(f"{BASE_URL}/{endpoint}?{qs}", headers=headers)


def _skillssh_request(path: str, params: dict | None = None) -> urllib.request.Request:
    qs = "?" + urllib.parse.urlencode(params) if params else ""
    return urllib.request.Request(
        f"{SKILLSSH_URL}{path}{qs}", headers={"User-Agent": f"skillsmp-cli/{__version__}"}
    )


def _search_request(source: str, params: dict) -> urllib.request.Request:
    if source == "skills.sh":
        return _skillssh_request("/api/search", params)
    return _api_url_request("search", params)


def _api_request(
    endpoint: str, params: dict, *, use_json_errors: bool = False
) -> dict:
    req = _api_url_request(endpoint, params)
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
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
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


def _from_skillssh(item: dict) -> dict:
    """Reshape a skills.sh search hit to look like a SkillsMP one."""
    repo = item.get("source", "")
    slug = item.get("skillId") or item.get("name") or "unknown"
    skill_id = item.get("id") or f"{repo}/{slug}"
    return {
        "name": slug,
        "author": repo.split("/")[0] or "unknown",
        "description": item.get("description", ""),
        "installs": item.get("installs"),
        # Sources without a slash are well-known sites like docs.stripe.com.
        "githubUrl": f"https://github.com/{repo}" if repo.count("/") == 1 else "",
        "skillUrl": f"{SKILLSSH_URL}/{skill_id}",
        "registry": "skills.sh",
        "id": skill_id,
        "repo": repo,
    }


def _install_command(d: dict) -> str:
    if d["registry"] == "skills.sh":
        repo = d["repo"] or ""
        target = repo if repo.count("/") == 1 else f"https://{repo}"
        return f"npx skills add {target} --skill {d['name']}"
    if d["githubUrl"]:
        return f"npx skills add {d['githubUrl']}"
    return ""


def _normalize_skill(skill: dict) -> dict:
    registry = skill.get("registry", "skillsmp")
    d = {
        "name": skill.get("name", "unknown"),
        "author": skill.get("author", "unknown"),
        "description": skill.get("description", ""),
        "stars": skill.get("stars", 0 if registry == "skillsmp" else None),
        "installs": skill.get("installs"),
        "updatedAt": skill.get("updatedAt"),
        "githubUrl": skill.get("githubUrl", ""),
        "skillUrl": skill.get("skillUrl", ""),
        "contentLanguage": skill.get("contentLanguage"),
        "registry": registry,
        "repo": skill.get("repo"),
    }
    d["id"] = skill.get("id") or f"{d['author']}/{d['name']}"
    d["installCommand"] = _install_command(d)
    return d


def _skill_stats(d: dict) -> str:
    if d["registry"] == "skills.sh":
        if d["installs"] is None:
            return "installs unknown"
        return f"{_format_stars(d['installs'])} installs"
    return f"{_format_stars(d['stars'])} stars, updated {_format_timestamp(d['updatedAt'])}"


def _frontmatter_description(text: str) -> str:
    """The description from SKILL.md's YAML frontmatter, if there is one."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    lines = text[3:end if end != -1 else None].splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("description:"):
            continue
        value = line[len("description:"):].strip()
        if value in ("", ">", "|", ">-", "|-", ">+", "|+"):
            # A block scalar: the indented lines that follow.
            block = []
            for nxt in lines[i + 1 :]:
                if nxt and not nxt[0].isspace():
                    break
                block.append(nxt.strip())
            value = " ".join(x for x in block if x)
        return value.strip().strip("\"'")
    return ""


def _skill_label(d: dict) -> str:
    """The skill's ID, tagged with the content language unless it's English."""
    label = d["id"]
    lang = d["contentLanguage"]
    if lang and lang != "en":
        label += f" ({lang})"
    return label


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace (descriptions can span lines) and cut to `limit`."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _print_skill(skill: dict) -> None:
    d = _normalize_skill(skill)

    print(f"  {_skill_label(d)}  [{_skill_stats(d)}]")
    if d["description"]:
        print(f"    {_clip(d['description'], DESC_DISPLAY_LIMIT)}")
    if d["githubUrl"]:
        print(f"    github: {d['githubUrl']}")
    if d["skillUrl"]:
        print(f"    {d['registry']}: {d['skillUrl']}")
    if d["registry"] == "skills.sh":
        print(f"    install: {d['installCommand']}")
    print()


def _print_skill_plain(skill: dict) -> None:
    d = _normalize_skill(skill)
    parts = [
        d["id"],
        str(d["installs"] if d["registry"] == "skills.sh" else d["stars"]),
        _clip(d["description"], DESC_PLAIN_LIMIT),
        d["githubUrl"],
        d["contentLanguage"] or "",
        d["registry"],
    ]
    print("\t".join(parts))


# --- commands ---


def _describe_pagination(pagination: dict) -> str:
    total = pagination.get("total", 0)
    pg = pagination.get("page", 1)
    if pagination.get("totalIsExact", True):
        return f"{total} results (page {pg}/{pagination.get('totalPages', 1)})"
    # The API only counts a little past the current page, so its total and
    # totalPages are lower bounds. Report what's known instead.
    if pagination.get("hasNext"):
        return f"page {pg}, more results on page {pg + 1}"
    return f"page {pg}, last page"


def _is_excluded(skill: dict, exclude: list[str]) -> bool:
    d = _normalize_skill(skill)
    haystack = f"{d['id']}\n{d['author']}/{d['name']}\n{d['description']}".lower()
    return any(term.lower() in haystack for term in exclude)


def _search(
    params: dict,
    exclude: list[str],
    *,
    limit: int,
    page: int,
    request_json: Callable[[dict], dict],
    stream: bool = False,
    max_api_pages: int = EXCLUDE_MAX_REQUESTS,
    source: str = "skillsmp",
) -> tuple[list[dict], dict, int]:
    """Run one search page through `request_json` (search params -> response).

    Returns (skills, pagination, excluded_count). With exclusions, or with
    `stream`, the API is read in pages of MAX_LIMIT and display pages are cut
    from what's kept, so page 2 starts where page 1 ended. `max_api_pages`
    caps how far into the API's pages that reading goes.

    For skills.sh, `params` is {"q", "sort"}; for SkillsMP, the API's own.
    """
    if source == "skills.sh":
        return _search_skillssh(params, exclude, limit=limit, page=page, request_json=request_json)
    if not exclude and not stream:
        data = request_json({**params, "limit": limit, "page": page}).get("data", {})
        return data.get("skills", []), data.get("pagination", {}), 0

    kept: list[dict] = []
    excluded = 0
    api_page = 1
    api_has_next = True
    # One extra kept result tells us whether another page exists.
    while (
        len(kept) <= page * limit
        and api_has_next
        and api_page <= max_api_pages
    ):
        data = request_json({**params, "limit": MAX_LIMIT, "page": api_page}).get("data", {})
        for s in data.get("skills", []):
            if _is_excluded(s, exclude):
                excluded += 1
            else:
                kept.append(s)
        api_pagination = data.get("pagination", {})
        api_has_next = api_pagination.get(
            "hasNext", api_page < api_pagination.get("totalPages", 1)
        )
        api_page += 1

    start = (page - 1) * limit
    pagination = {
        "total": len(kept),
        "totalIsExact": not api_has_next,
        "page": page,
        "totalPages": max(1, -(-len(kept) // limit)),
        "hasNext": api_has_next or len(kept) > page * limit,
    }
    return kept[start : start + limit], pagination, excluded


def _search_skillssh(
    params: dict,
    exclude: list[str],
    *,
    limit: int,
    page: int,
    request_json: Callable[[dict], dict],
) -> tuple[list[dict], dict, int]:
    """One request returns every hit skills.sh has; pages are cut from it."""
    query = params["q"]
    if len(query.strip()) < 2:
        hits: list[dict] = []  # skills.sh rejects one-character queries
    else:
        data = request_json({"q": query, "limit": SKILLSSH_MAX_RESULTS})
        hits = [_from_skillssh(s) for s in data.get("skills", [])]
    if params.get("sort") == "installs":
        hits.sort(key=lambda s: s["installs"] or 0, reverse=True)
    kept = [s for s in hits if not _is_excluded(s, exclude)]
    start = (page - 1) * limit
    pagination = {
        "total": len(kept),
        # A full result set means skills.sh probably cut it off.
        "totalIsExact": len(hits) < SKILLSSH_MAX_RESULTS,
        "page": page,
        "totalPages": max(1, -(-len(kept) // limit)),
        "hasNext": len(kept) > page * limit,
    }
    return kept[start : start + limit], pagination, len(hits) - len(kept)


def _search_params(
    source: str,
    query: str,
    sort: str,
    category: str | None = None,
    occupation: str | None = None,
    language: str | None = None,
) -> dict:
    if source == "skills.sh":
        return {"q": query, "sort": sort}
    return {
        "q": query,
        "sortBy": sort,
        "category": category,
        "occupation": occupation,
        "language": language,
    }


def _warn(msg: str) -> None:
    print(f"skillsmp: {msg}", file=sys.stderr)


def _cmd_search(
    query: str,
    *,
    limit: int = 10,
    page: int = 1,
    sort: str | None = None,
    category: str | None = None,
    occupation: str | None = None,
    language: str | None = None,
    exclude: list[str] | None = None,
    source: str = "skillsmp",
    fallback: bool = False,
    output_json: bool = False,
    output_plain: bool = False,
) -> None:
    def run(src: str):
        params = _search_params(
            src, query, sort or DEFAULT_SORT[src], category, occupation, language
        )
        if src == "skills.sh" and fallback:
            # Raise instead of exiting, so a failure can fall back.
            request_json = lambda p: json.loads(
                _fetch(_search_request(src, p), on_retry=_warn)
            )
        elif src == "skills.sh":
            request_json = lambda p: json.loads(
                _fetch_with_retry(_search_request(src, p), use_json_errors=output_json)
            )
        else:
            request_json = lambda p: _api_request("search", p, use_json_errors=output_json)
        return _search(
            params, exclude or [], limit=limit, page=page, request_json=request_json, source=src
        )

    used = source
    fell_back = None
    try:
        skills, pagination, excluded = run(source)
    except FetchError as e:  # only raised when falling back is allowed
        skills, pagination, excluded = [], {}, 0
        fell_back = f"skills.sh failed ({e})"
    if fallback and source == "skills.sh" and page == 1 and not skills:
        fell_back = fell_back or "no results on skills.sh"
        if not output_json:
            _warn(f"{fell_back}; searching SkillsMP")
        used = "skillsmp"
        sort = None
        skills, pagination, excluded = run(used)
    else:
        fell_back = None

    if output_json:
        extra = {"exclude": exclude, "excluded": excluded} if exclude else {}
        if fell_back:
            extra["fallbackReason"] = fell_back
        json.dump(
            {
                "query": query,
                "source": used,
                "mode": "keyword",
                "total": pagination.get("total", 0),
                "totalIsExact": pagination.get("totalIsExact", True),
                "page": pagination.get("page", 1),
                "totalPages": pagination.get("totalPages", 1),
                "hasNext": pagination.get("hasNext", False),
                **extra,
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

    summary = _describe_pagination(pagination)
    if excluded:
        summary += f", {excluded} excluded"
    print(f'{SOURCE_LABELS[used]} search: "{query}" — {summary}\n')
    if not skills:
        print("  No results found.")
        if _stderr_is_tty():
            print("\n  Tip: try fewer or broader keywords.", file=sys.stderr)
        return
    for s in skills:
        _print_skill(s)


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
    print("Filter searches with: skillsmp --category <slug> <query>")


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
        print(
            f"{len(slugs)} occupations "
            "(filter searches with: skillsmp --occupation <slug> <query>)\n"
        )
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


def _fetch_first(urls) -> str | None:
    """The body of the first URL that answers, or None."""
    for url in urls:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"skillsmp-cli/{__version__}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
    return None


def _fetch_skill_md(github_url: str) -> str | None:
    return _fetch_first(_github_raw_urls(github_url))


def _find_skill(author: str, name: str, *, use_json_errors: bool = False) -> list[dict]:
    """Search for skills named exactly author/name, English and most-starred first."""
    # Searching by name alone ranks by stars, so a low-star copy of a common
    # name (say, code-review) falls past the first page. Including the author
    # in the query finds it; the name-only query is a fallback.
    for query in (f"{author} {name}", name):
        result = _api_request(
            "search", {"q": query, "limit": MAX_LIMIT}, use_json_errors=use_json_errors
        )
        matches = [
            s
            for s in result.get("data", {}).get("skills", [])
            if s.get("author", "").lower() == author.lower()
            and s.get("name", "").lower() == name.lower()
        ]
        if matches:
            # Translations share author/name; show the English one first.
            return sorted(matches, key=lambda s: s.get("contentLanguage") not in ("en", None))
    return []


def _skillssh_files(d: dict) -> list[dict] | None:
    """Every file in a skills.sh skill's folder, or None if unavailable."""
    repo = d.get("repo") or ""
    if repo.count("/") != 1:
        return None  # Downloads only cover GitHub-hosted skills.
    path = "/api/download/" + "/".join(
        urllib.parse.quote(part, safe="") for part in (*repo.split("/"), d["name"])
    )
    try:
        data = json.loads(_fetch(_skillssh_request(path)))
    except (FetchError, ValueError):
        return None
    files = data.get("files")
    return files if isinstance(files, list) else None


def _get_skill_md(d: dict) -> str | None:
    """A skill's SKILL.md: from skills.sh's cache, or straight from GitHub."""
    if d["registry"] == "skills.sh":
        repo = d.get("repo") or ""
        if repo.count("/") != 1:
            # A site like docs.stripe.com, which publishes its skills under
            # /.well-known/ (the newer path first, as `npx skills` does).
            name = urllib.parse.quote(d["name"], safe="")
            return _fetch_first(
                f"https://{repo}/.well-known/{path}/{name}/SKILL.md"
                for path in ("agent-skills", "skills")
            )
        for f in _skillssh_files(d) or []:
            if f.get("path") == "SKILL.md":
                return f.get("contents")
        return None
    return _fetch_skill_md(d["githubUrl"])


def _find_skill_skillssh(parts: list[str]) -> list[dict]:
    """skills.sh hits for owner/name or owner/repo/name, most installed first."""
    owner, name = parts[0], parts[-1]
    params = {"q": name, "owner": owner, "limit": SKILLSSH_MAX_RESULTS}
    if "." in owner:
        # A site like docs.stripe.com, which the owner filter rejects
        # (GitHub names have no dots); match it below instead.
        del params["owner"]
    data = json.loads(_fetch(_skillssh_request("/api/search", params), on_retry=_warn))
    repo = "/".join(parts[:2]).lower() if len(parts) == 3 else None
    matches = [
        hit
        for hit in map(_from_skillssh, data.get("skills", []))
        if hit["name"].lower() == name.lower()
        and hit["author"].lower() == owner.lower()
        and (repo is None or hit["repo"].lower() == repo)
    ]
    return sorted(matches, key=lambda s: s["installs"] or 0, reverse=True)


def _show_error(msg: str, *, output_json: bool) -> NoReturn:
    if output_json:
        json.dump({"error": msg}, sys.stdout, indent=2)
        print()
    else:
        _warn(msg)
    raise SystemExit(1)


def _cmd_show(
    spec: str, *, output_json: bool = False, source: str = "skillsmp", fallback: bool = False
) -> None:
    parts = spec.split("/")
    matches: list[dict] = []
    guessed = False
    if source == "skills.sh" or len(parts) == 3:
        try:
            matches = _find_skill_skillssh(parts)
        except FetchError as e:
            if not fallback or len(parts) == 3:
                _show_error(str(e), output_json=output_json)
        if not matches and len(parts) == 3:
            # Not in search results; the download below decides if it exists.
            matches = [_from_skillssh({"source": "/".join(parts[:2]), "skillId": parts[2]})]
            guessed = True
    if not matches and len(parts) == 2 and (source == "skillsmp" or fallback):
        matches = _find_skill(parts[0], parts[1], use_json_errors=output_json)

    if not matches:
        _show_error(f"skill not found: {spec}", output_json=output_json)

    d = _normalize_skill(matches[0])
    others = [_normalize_skill(s) for s in matches[1:]]
    skill_md = _get_skill_md(d)
    if guessed and skill_md is None:
        _show_error(f"skill not found: {spec}", output_json=output_json)
    if not d["description"] and skill_md:
        d["description"] = _frontmatter_description(skill_md)

    if output_json:
        d["skillMd"] = skill_md
        d["otherMatches"] = [o["skillUrl"] for o in others]
        json.dump(d, sys.stdout, indent=2)
        print()
        return

    print(f"{_skill_label(d)}  [{_skill_stats(d)}]\n")
    if d["description"]:
        print(f"{d['description']}\n")
    rows = [
        ("github", d["githubUrl"]),
        (d["registry"], d["skillUrl"]),
        ("install", d["installCommand"] if d["registry"] == "skills.sh" else ""),
    ]
    for label, value in rows:
        if value:
            print(f"{label + ':':<11}{value}")
    if others:
        _warn(f"{spec} also matched (showing the top result):")
        for o in others:
            detail = o["contentLanguage"] or "unknown language"
            if o["registry"] == "skills.sh":
                detail = _skill_stats(o)
            print(f"  {o['skillUrl']}  ({detail})", file=sys.stderr)
    if skill_md is not None:
        print(f"\n--- SKILL.md ---\n{skill_md}")
    else:
        where = "skills.sh" if d["registry"] == "skills.sh" else "GitHub"
        _warn(f"could not fetch SKILL.md from {where}")


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
    limit: int | None = None
    page: int | None = None
    sort: str | None = None
    filters: dict[str, str] = {}
    exclude: list[str] = []
    source: str | None = None
    interactive = False
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
            _die("AI semantic search was retired by SkillsMP; use keyword search instead")
        elif arg in ("-i", "--interactive"):
            interactive = True
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
        elif arg in FILTER_FLAGS:
            filters[arg[2:]], i = _take_value(argv, i, arg)
        elif arg == "--source":
            source, i = _take_value(argv, i, arg)
            if source not in SOURCES:
                _die(f"--source must be 'skills.sh' or 'skillsmp' (got: {source})")
        elif arg in ("-x", "--exclude"):
            term, i = _take_value(argv, i, arg)
            if not term.strip():
                _die(f"{arg} requires a non-empty value")
            exclude.append(term.strip())
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

    if interactive:
        if command != "search":
            _die(f"--interactive does not apply to the {command} command")
        if output_json or output_plain:
            _die("--interactive cannot be combined with --json or --plain")
        if not (_isatty(sys.stdin) and _isatty(sys.stdout)):
            _die("--interactive needs a terminal")

    # No args at all: concise help.
    if command == "search" and not query_parts and not interactive:
        print(_concise_help(_stderr_is_tty()), file=sys.stderr)
        raise SystemExit(2)

    # Validate.
    if output_json and output_plain:
        _die("--json and --plain are mutually exclusive")

    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        _die(f"--limit must be 1-{MAX_LIMIT} (got: {limit})")

    all_sorts = SORTS["skills.sh"] + SORTS["skillsmp"]
    if sort is not None and sort not in all_sorts:
        _die(f"--sort must be one of {', '.join(all_sorts)} (got: {sort})")

    if command != "search" and (
        filters or exclude or any(x is not None for x in (limit, page, sort))
    ):
        _die(
            "--limit, --page, --sort, --category, --occupation, --language, --exclude "
            f"do not apply to the {command} command"
        )

    if command in ("categories", "occupations") and query_parts:
        _die(f"the {command} command takes no arguments")

    if command in ("categories", "occupations") and source is not None:
        _die(f"--source does not apply to the {command} command (it lists SkillsMP's)")

    if command == "show":
        spec = query_parts[0] if query_parts else ""
        parts = spec.split("/")
        if len(query_parts) != 1 or len(parts) not in (2, 3) or not all(parts):
            _die("show requires an <author>/<skill> or <owner>/<repo>/<skill> argument")
        if output_plain:
            _die("--plain does not apply to the show command")
        if len(parts) == 3 and source == "skillsmp":
            _die("<owner>/<repo>/<skill> is a skills.sh ID; use <author>/<skill> for SkillsMP")

    # skills.sh is the default; SkillsMP-only options pick SkillsMP instead,
    # and an explicit --source turns off falling back to the other one.
    skillsmp_only = [f"--{name}" for name in filters]
    if sort in SORTS["skillsmp"]:
        skillsmp_only.append(f"--sort {sort}")
    fallback = False
    if source is None:
        source = "skillsmp" if skillsmp_only else "skills.sh"
        fallback = source == "skills.sh"
    elif source == "skills.sh" and skillsmp_only:
        _die(f"{skillsmp_only[0]} needs --source skillsmp")
    elif source == "skillsmp" and sort in SORTS["skills.sh"]:
        _die(f"--sort {sort} needs --source skills.sh")

    return {
        "mode": "interactive" if interactive else command,
        "query": " ".join(query_parts),
        # The TUI has room for a longer page.
        "limit": limit if limit is not None else (20 if interactive else 10),
        "page": page if page is not None else 1,
        "sort": sort if sort is not None else DEFAULT_SORT[source],
        "source": source,
        "fallback": fallback,
        "category": filters.get("category"),
        "occupation": filters.get("occupation"),
        "language": filters.get("language"),
        "exclude": exclude,
        "json": output_json,
        "plain": output_plain,
    }


# --- entry point ---


def main() -> None:
    try:
        _run()
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (e.g. `skillsmp occupations | head`). Point
        # stdout at devnull so the interpreter's final flush can't raise again,
        # then exit like a process killed by SIGPIPE.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        raise SystemExit(141)


def _run() -> None:
    args = _parse_args(sys.argv[1:])
    mode = args["mode"]

    if mode == "categories":
        _cmd_categories(output_json=args["json"], output_plain=args["plain"])
    elif mode == "occupations":
        _cmd_occupations(output_json=args["json"], output_plain=args["plain"])
    elif mode == "show":
        _cmd_show(
            args["query"],
            output_json=args["json"],
            source=args["source"],
            fallback=args["fallback"],
        )
    elif mode == "interactive":
        # Imported here so plain searches don't pay Textual's startup cost.
        from skillsmp.tui import run

        run(args)
    else:
        _cmd_search(
            args["query"],
            limit=args["limit"],
            page=args["page"],
            sort=args["sort"],
            category=args["category"],
            occupation=args["occupation"],
            language=args["language"],
            exclude=args["exclude"],
            source=args["source"],
            fallback=args["fallback"],
            output_json=args["json"],
            output_plain=args["plain"],
        )
