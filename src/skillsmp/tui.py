"""Full-screen browser for skills.sh and SkillsMP search results (`skillsmp -i`)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

import skillsmp as core

SEARCH_DEBOUNCE = 0.2
# Searches are metered (the Free plan gets 50 a day), so only prefetch while
# there's plenty left.
PREFETCH_MIN_REMAINING = 10
SKILL_MD_DEBOUNCE = 0.15
# Dracula's red, for skills whose GitHub repo is gone.
GONE_STYLE = "#ff5555"
# Textual's Markdown widget gets sluggish on huge documents.
SKILL_MD_MAX_CHARS = 60_000


@dataclass(frozen=True)
class Search:
    query: str
    exclude: tuple[str, ...] = ()
    sort: str = "relevance"
    page: int = 1
    limit: int = 20
    category: str | None = None
    occupation: str | None = None
    language: str | None = None
    source: str = "skills.sh"
    # Try SkillsMP when skills.sh has nothing (or fails) on page 1.
    fallback: bool = False

    def params(self) -> dict:
        return core._search_params(
            self.source, self.query, self.sort, self.category, self.occupation, self.language
        )

    def on_skillsmp(self) -> Search:
        return replace(self, source="skillsmp", sort=core.DEFAULT_SORT["skillsmp"], fallback=False)


def parse_query(text: str) -> tuple[str, tuple[str, ...]]:
    """Split search-box text into the query and its `-term` exclusions."""
    try:
        tokens = shlex.split(text)
    except ValueError:  # an unclosed quote while typing
        tokens = text.split()
    words: list[str] = []
    exclude: list[str] = []
    for token in tokens:
        if token.startswith("-") and len(token) > 1:
            exclude.append(token[1:])
        else:
            words.append(token)
    return " ".join(words), tuple(dict.fromkeys(exclude))


def format_query(query: str, exclude: tuple[str, ...]) -> str:
    return " ".join([query] * bool(query) + ["-" + shlex.quote(t) for t in exclude])


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


class _NotCached(Exception):
    pass


class Client:
    """Caching, thread-safe access to both search APIs and SKILL.md files.

    Raw API responses are cached rather than result pages, so changing
    exclusions or paging back re-filters what's already here without a request.
    """

    def __init__(self, on_retry=None) -> None:
        self.on_retry = on_retry
        # (remaining, limit, "today" or "this month") from the last response.
        self.quota: tuple[int, int, str] | None = None
        self.exhausted = False
        self._lock = threading.Lock()
        self._raw: dict[tuple, dict] = {}
        self._inflight: dict[tuple, threading.Event] = {}
        self._skill_md: dict[str, str | None] = {}
        # {"updatedAt", "repoMissing"} for skills.sh skills, by skill ID.
        self.dates: dict[str, dict] = {}

    @staticmethod
    def _key(source: str, params: dict) -> tuple:
        return (source, *sorted((k, str(v)) for k, v in params.items() if v is not None))

    def _request_json(self, source: str, params: dict) -> dict:
        key = self._key(source, params)
        while True:
            with self._lock:
                if key in self._raw:
                    return self._raw[key]
                event = self._inflight.get(key)
                owner = event is None
                if owner:
                    event = self._inflight[key] = threading.Event()
            if not owner:
                # Someone else (usually a prefetch) is fetching this page.
                # Wait, then re-check; if they failed, we try ourselves.
                event.wait()
                continue
            try:
                req = core._search_request(source, params)
                # Only SkillsMP meters searches.
                on_headers = self._track_quota if source == "skillsmp" else None
                try:
                    raw = core._fetch(req, on_retry=self.on_retry, on_headers=on_headers)
                except core.FetchError as e:
                    if e.code in core.QUOTA_ERROR_CODES:
                        self.exhausted = True
                    raise
                data = json.loads(raw)
                with self._lock:
                    self._raw[key] = data
                return data
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                event.set()

    def _track_quota(self, headers: dict) -> None:
        h = {k.lower(): v for k, v in headers.items()}
        for period, label in (("daily", "today"), ("monthly", "this month")):
            remaining = h.get(f"x-ratelimit-{period}-remaining")
            limit = h.get(f"x-ratelimit-{period}-limit")
            if remaining is not None and limit is not None:
                try:
                    self.quota = (int(remaining), int(limit), label)
                except ValueError:
                    pass
                return

    def can_prefetch(self, source: str) -> bool:
        if source == "skills.sh":
            return True
        if self.exhausted:
            return False
        return self.quota is None or self.quota[0] > PREFETCH_MIN_REMAINING

    def _cached_json(self, source: str, params: dict) -> dict:
        with self._lock:
            data = self._raw.get(self._key(source, params))
        if data is None:
            raise _NotCached
        return data

    def _run(self, search: Search, fetch):
        # SkillsMP is read 50 at a time and skills.sh 200 at once; display
        # pages are cut from that, and exclusions just re-filter it.
        needed = -(-search.page * search.limit // core.MAX_LIMIT)
        return core._search(
            search.params(),
            list(search.exclude),
            limit=search.limit,
            page=search.page,
            request_json=lambda params: fetch(search.source, params),
            stream=True,
            max_api_pages=needed + core.EXCLUDE_MAX_REQUESTS,
            source=search.source,
        )

    @staticmethod
    def _falls_back(search: Search, result) -> bool:
        return (
            search.fallback
            and search.source == "skills.sh"
            and search.page == 1
            and (result is None or not result[0])
        )

    def search(self, search: Search):
        """Blocking search, returning (search actually run, result).

        That's a SkillsMP search when `search` falls back. Raises
        core.FetchError.
        """
        try:
            result = self._run(search, self._request_json)
        except core.FetchError:
            if not self._falls_back(search, None):
                raise
            result = None
        if not self._falls_back(search, result):
            return search, result
        fallback = search.on_skillsmp()
        return fallback, self._run(fallback, self._request_json)

    def search_cached(self, search: Search):
        """Like search(), if everything it needs is cached; else None."""
        try:
            result = self._run(search, self._cached_json)
        except _NotCached:
            return None
        if not self._falls_back(search, result):
            return search, result
        fallback = search.on_skillsmp()
        try:
            return fallback, self._run(fallback, self._cached_json)
        except _NotCached:
            return None

    def skill_md(self, d: dict) -> str | None:
        key = d["skillUrl"]
        with self._lock:
            if key in self._skill_md:
                return self._skill_md[key]
        text = core._get_skill_md(d)
        with self._lock:
            self._skill_md[key] = text
        return text

    def commit_dates(self, skills: list[dict]) -> dict[str, dict]:
        """Look up and remember skills' last-commit dates; raises core.FetchError."""
        with self._lock:
            todo = [d for d in skills if d["id"] not in self.dates]
            skill_mds = {
                d["id"]: self._skill_md[d["skillUrl"]]
                for d in todo
                if self._skill_md.get(d["skillUrl"]) is not None
            }
        if todo:
            found = core._commit_dates(todo, skill_mds)
            with self._lock:
                self.dates.update(found)
        with self._lock:
            return {d["id"]: self.dates.get(d["id"]) for d in skills}

    def skill_md_cached(self, d: dict) -> tuple[bool, str | None]:
        with self._lock:
            return d["skillUrl"] in self._skill_md, self._skill_md.get(d["skillUrl"])


def _parse_time(ts) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age(ts, now: datetime | None = None) -> str:
    """How long ago, in a few characters: 5d, 3w, 7mo, 2y."""
    when = _parse_time(ts)
    if when is None:
        return ""
    days = ((now or datetime.now(timezone.utc)) - when).days
    if days < 1:
        return "today"
    if days < 14:
        return f"{days}d"
    if days < 60:
        return f"{days // 7}w"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def _detail_text(d: dict, description: str = "", dates_pending: bool = False) -> Text:
    t = Text()
    t.append(d["id"].rpartition("/")[0] + "/", style="dim")
    t.append(d["name"], style="bold")
    t.append("\n")
    if d["registry"] == "skills.sh":
        if d["installs"] is None:
            meta = ["installs unknown"]
        else:
            meta = [f"{core._format_stars(d['installs'])} installs"]
        if d["updatedAt"]:
            age = _age(d["updatedAt"])
            ago = age if age == "today" else f"{age} ago"
            meta.append(f"updated {core._format_timestamp(d['updatedAt'])} ({ago})")
        elif d["repoMissing"]:
            meta.append(Text("GitHub repo deleted", style=GONE_STYLE))
        elif dates_pending:
            meta.append("updated …")
        else:
            meta.append("last commit unknown")
    else:
        meta = [core._skill_stats(d).replace(", ", "  ·  ")]
    meta.append(f"from {core.SOURCE_LABELS[d['registry']]}")
    if d["contentLanguage"]:
        meta.append(f"lang {d['contentLanguage']}")
    items = [Text(m, style="dim") if isinstance(m, str) else m for m in meta]
    t.append_text(Text("  ·  ", style="dim").join(items))
    t.append("\n\n")
    description = d["description"] or description
    if description:
        t.append(" ".join(description.split()))
        t.append("\n\n")
    rows = (("github ", d["githubUrl"]), ("web    ", d["skillUrl"]), ("install", d["installCommand"]))
    for label, value in rows:
        if value:
            t.append(f"{label} ", style="dim")
            t.append(value, style="bold" if label == "install" else "underline")
            t.append("\n")
    return t


def _metric(d: dict) -> str:
    if d["registry"] == "skills.sh":
        return f"↓ {core._format_stars(d['installs'])}" if d["installs"] is not None else ""
    return f"★ {core._format_stars(d['stars'])}"


def _option(d: dict) -> Option:
    name = Text(no_wrap=True, overflow="ellipsis")
    name.append(f"{d['author']}/", style="dim")
    name.append(d["name"], style="bold")
    if d["contentLanguage"] and d["contentLanguage"] != "en":
        name.append(f" {d['contentLanguage']}", style="dim italic")
    row = Table.grid(expand=True, padding=(0, 1))
    row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    row.add_column(justify="right", no_wrap=True)
    row.add_column(justify="right", no_wrap=True, width=5)
    if d["repoMissing"]:
        age = Text("gone", style=GONE_STYLE)
    else:
        age = Text(_age(d["updatedAt"]), style="dim")
    row.add_row(name, Text(_metric(d), style="dim"), age)
    return Option(row)


def _missing_skill_md(d: dict) -> str:
    where = "skills.sh" if d["registry"] == "skills.sh" else "GitHub"
    return f"*Couldn't fetch SKILL.md from {where}.*"


class Results(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "first", show=False),
        Binding("G", "last", show=False),
    ]


class SkillScreen(Screen):
    """Full-screen SKILL.md reader."""

    BINDINGS = [
        Binding("escape,q,enter", "app.pop_screen", "Back"),
        Binding("o", "open('githubUrl')", "GitHub"),
        Binding("w", "open('skillUrl')", "Web page"),
        Binding("i", "copy_install", "Copy install"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("j", "scroll('down')", show=False),
        Binding("k", "scroll('up')", show=False),
    ]

    def __init__(self, skill: dict, markdown: str, description: str = "") -> None:
        super().__init__()
        self.skill = skill
        self.markdown = markdown
        self.description = description

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="reader"):
            yield Static(_detail_text(self.skill, self.description), id="reader-meta")
            yield Markdown(self.markdown)
        yield Footer()

    def on_mount(self) -> None:
        reader = self.query_one("#reader")
        reader.border_title = self.skill["id"]
        reader.focus()

    def action_scroll(self, direction: str) -> None:
        reader = self.query_one("#reader", VerticalScroll)
        (reader.scroll_down if direction == "down" else reader.scroll_up)()

    def action_open(self, field: str) -> None:
        open_skill_url(self.app, self.skill, field)

    def action_copy_url(self) -> None:
        copy_text(self.app, self.skill["githubUrl"] or self.skill["skillUrl"])

    def action_copy_install(self) -> None:
        copy_text(self.app, self.skill["installCommand"])


def open_skill_url(app: App, d: dict, field: str) -> None:
    if d[field]:
        app.open_url(d[field])


def copy_text(app: App, text: str) -> None:
    if not text:
        return
    app.copy_to_clipboard(text)  # OSC 52, which works over ssh
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=text.encode(), check=False)
    app.notify(f"Copied {text}")


class SkillsmpApp(App):
    TITLE = "skillsmp"
    CSS = """
    #query { margin: 0 1; }
    #status { height: 1; padding: 0 2; color: $text-muted; }
    #status.error { color: $error; }
    #main { height: 1fr; }
    #results, #detail, #reader {
        border: round $panel-lighten-2;
        border-title-color: $text-muted;
        background: $surface;
    }
    #results:focus, #detail:focus, #reader:focus {
        border: round $primary;
        border-title-color: $primary;
    }
    #results { width: 42%; min-width: 34; height: 1fr; max-height: 100%; margin-left: 1; }
    #results.stale { opacity: 0.45; }
    #detail { width: 1fr; margin-right: 1; padding: 0 1; }
    #reader { margin: 0 1; padding: 0 1; }
    #meta, #reader-meta { margin-bottom: 1; }
    #skillmd, Markdown { background: $surface; padding: 0; margin: 0; }
    """

    BINDINGS = [
        Binding("/", "focus_query", "Search"),
        Binding("escape", "focus_results", "Results", key_display="esc"),
        Binding("right,]", "page(1)", "Next", key_display="→"),
        Binding("left,[", "page(-1)", "Prev", key_display="←"),
        Binding("s", "toggle_sort", "Sort"),
        Binding("b", "toggle_source", "Source"),
        Binding("x", "hide('name')", "Hide name"),
        Binding("X", "hide('author')", "Hide author"),
        Binding("o", "open('githubUrl')", "GitHub"),
        Binding("w", "open('skillUrl')", "Web page", show=False),
        Binding("i", "copy_install", "Copy install"),
        Binding("y", "copy_url", "Copy URL", show=False),
        Binding("q", "quit", "Quit"),
    ]

    # Actions that act on the results list, so are off while the reader is up.
    MAIN_ACTIONS = {
        "focus_query", "focus_results", "page", "toggle_sort", "toggle_source", "hide",
        "open", "copy_url", "copy_install",
    }

    def __init__(self, search: Search, client: Client | None = None) -> None:
        super().__init__()
        # Start in the results when there's a query, else in the search box.
        self.AUTO_FOCUS = "#results" if search.query else "#query"
        # New queries start from here: the chosen source, sort and fallback.
        # self.search can differ, e.g. after falling back to SkillsMP.
        self.home = search
        self.search = search
        self.client = client or Client(on_retry=self._notify_retry)
        self.skills: list[dict] = []
        self.pagination: dict = {}
        self.excluded = 0
        self.loading_search: Search | None = None
        self._search_timer: Timer | None = None
        self._skill_md_timer: Timer | None = None
        self._shown_skill_md: str | None = None
        self._warned_no_token = False

    # --- layout ---

    def compose(self) -> ComposeResult:
        yield Input(
            format_query(self.search.query, self.search.exclude),
            placeholder="Search skills   (-term hides results containing term)",
            id="query",
        )
        yield Static(id="status")
        with Horizontal(id="main"):
            yield Results(id="results")
            with VerticalScroll(id="detail"):
                yield Static(id="meta")
                yield Markdown(id="skillmd")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "dracula"
        # Held directly: App.query_one searches the active screen, which is
        # the SKILL.md reader while it's open.
        self.box = self.query_one("#query", Input)
        self.status = self.query_one("#status", Static)
        self.results = self.query_one("#results", Results)
        self.detail = self.query_one("#detail", VerticalScroll)
        self.meta = self.query_one("#meta", Static)
        self.skill_md_view = self.query_one("#skillmd", Markdown)
        self.results.border_title = "Results"
        self.detail.border_title = "Skill"
        if self.search.query:
            self.go(self.search)
        else:
            self.set_status("Type a query and press Enter.")

    # --- searching ---

    def go(self, search: Search) -> None:
        """Show `search`: instantly from cache, otherwise in the background."""
        self.search = search
        cached = self.client.search_cached(search)
        if cached is not None:
            self.show_results(search, *cached)
            return
        self.loading_search = search
        self.results.add_class("stale")
        self.set_status(f"Searching “{search.query}”…")
        self.refresh_bindings()
        self.fetch(search)

    @work(thread=True, exclusive=True, group="search")
    def fetch(self, search: Search) -> None:
        try:
            used, result = self.client.search(search)
        except core.FetchError as e:
            self.call_from_thread(self.show_error, search, str(e))
            return
        self.call_from_thread(self.show_results, search, used, result)

    @work(thread=True, group="prefetch")
    def prefetch(self, search: Search) -> None:
        try:
            self.client.search(search)
        except core.FetchError:
            pass  # The page will be fetched (and the error shown) if visited.

    def show_results(self, search: Search, used: Search, result) -> None:
        if search != self.search:
            return  # A newer search has replaced this one.
        if used != search:
            self.search = used
            self.notify(f"Nothing on skills.sh for “{search.query}”, so showing SkillsMP.")
        self.loading_search = None
        self.skills, self.pagination, self.excluded = result
        self.results.remove_class("stale")
        self.results.set_options([_option(self.normalize(s)) for s in self.skills])
        if self.skills:
            self.results.highlighted = 0
        self.show_detail(self.current())
        self.fetch_dates_for_page()
        self.set_status()
        self.refresh_bindings()
        following = replace(self.search, page=self.search.page + 1)
        if (
            self.pagination.get("hasNext")
            and self.client.search_cached(following) is None
            and self.client.can_prefetch(following.source)
        ):
            self.prefetch(following)

    def show_error(self, search: Search, message: str) -> None:
        if search != self.search:
            return
        self.loading_search = None
        self.results.remove_class("stale")
        self.set_status(message, error=True)
        self.refresh_bindings()
        self.notify(message, severity="error", timeout=8)

    def set_status(self, message: str | None = None, *, error: bool = False) -> None:
        status = self.status
        status.set_class(error, "error")
        if message is None:
            s = self.search
            parts = [core.SOURCE_LABELS[s.source]]
            if not self.skills:
                parts.append(f"no results for “{s.query}”")
            elif self.pagination.get("totalIsExact", True) or s.source == "skills.sh":
                # skills.sh's total is only inexact when it hit its cap.
                total = self.pagination.get("total", len(self.skills))
                pages = self.pagination.get("totalPages", 1)
                plus = "" if self.pagination.get("totalIsExact", True) else "+"
                parts += [f"{total}{plus} results", f"page {s.page}/{pages}"]
            else:
                more = "more →" if self.pagination.get("hasNext") else "last page"
                parts += [f"page {s.page}", more]
            parts.append(f"sort: {s.sort}")
            if self.excluded:
                parts.append(f"{self.excluded} hidden")
            if s.source == "skillsmp" and self.client.quota:
                remaining, limit, period = self.client.quota
                parts.append(f"{remaining}/{limit} searches left {period}")
            filters = [
                f"{k} {v}"
                for k, v in (("category", s.category), ("occupation", s.occupation), ("language", s.language))
                if v and s.source == "skillsmp"
            ]
            message = "  ·  ".join(parts + filters)
        status.update(message)

    # --- query box ---

    def search_from_box(self) -> Search | None:
        query, exclude = parse_query(self.box.value)
        if not query:
            return None
        return replace(self.home, query=query, exclude=exclude, page=1, limit=self.search.limit)

    @on(Input.Changed, "#query")
    def query_changed(self) -> None:
        if self._search_timer:
            self._search_timer.stop()
        self._search_timer = self.set_timer(SEARCH_DEBOUNCE, self.search_if_changed)

    def search_if_changed(self) -> None:
        """Apply typing when it costs no request (e.g. editing exclusions)."""
        search = self.search_from_box()
        if search is None or search == self.search:
            return
        if (search.query, search.exclude) == (self.search.query, self.search.exclude):
            return  # e.g. self.search is this search's SkillsMP fallback
        if self.client.search_cached(search) is not None:
            self.go(search)
        else:
            self.set_status(f"Press Enter to search “{search.query}”")

    @on(Input.Submitted, "#query")
    def query_submitted(self) -> None:
        if self._search_timer:
            self._search_timer.stop()
        search = self.search_from_box()
        if search is None:
            return
        if search != self.search or (self.loading_search is None and not self.skills):
            self.go(search)
        self.results.focus()

    # --- selection and detail pane ---

    def normalize(self, skill: dict) -> dict:
        d = core._normalize_skill(skill)
        if d["registry"] == "skills.sh":
            d.update(self.client.dates.get(d["id"], {}))
        return d

    def current(self) -> dict | None:
        index = self.results.highlighted
        if index is None or index >= len(self.skills):
            return None
        return self.normalize(self.skills[index])

    # --- last-commit dates ---

    def dates_pending(self, d: dict) -> bool:
        return d["registry"] == "skills.sh" and d["id"] not in self.client.dates

    def fetch_dates_for_page(self) -> None:
        todo = [d for d in map(self.normalize, self.skills) if self.dates_pending(d)]
        if todo:
            self.fetch_dates(todo)

    @work(thread=True, group="dates")
    def fetch_dates(self, skills: list[dict]) -> None:
        if not core._github_token():
            self.call_from_thread(self.warn_no_token, skills)
            return
        try:
            self.client.commit_dates(skills)
        except (core.FetchError, ValueError) as e:
            self.call_from_thread(
                self.notify, f"Couldn't get last-commit dates from GitHub ({e})", severity="warning"
            )
            return
        self.call_from_thread(self.dates_ready)

    def warn_no_token(self, skills: list[dict]) -> None:
        for d in skills:
            self.client.dates.setdefault(d["id"], {"updatedAt": None, "repoMissing": False})
        self.dates_ready()
        if not self._warned_no_token:
            self._warned_no_token = True
            self.notify(core._missing_token_hint().capitalize() + ".", severity="warning", timeout=8)

    def dates_ready(self) -> None:
        """Redraw rows (and the detail pane) now that their dates are in."""
        for i, skill in enumerate(self.skills):
            d = self.normalize(skill)
            if d["registry"] == "skills.sh":
                self.results.replace_option_prompt_at_index(i, _option(d).prompt)
        d = self.current()
        if d is not None:
            self.render_meta(d)

    def render_meta(self, d: dict) -> None:
        cached, text = self.client.skill_md_cached(d)
        description = core._frontmatter_description(text) if cached and text else ""
        self.meta.update(_detail_text(d, description, self.dates_pending(d)))

    @on(OptionList.OptionHighlighted, "#results")
    def highlighted(self) -> None:
        self.show_detail(self.current())
        self.refresh_bindings()

    def show_detail(self, d: dict | None) -> None:
        meta, detail = self.meta, self.detail
        if self._skill_md_timer:
            self._skill_md_timer.stop()
        if d is None:
            meta.update(Text("Nothing selected.", style="dim"))
            self.set_skill_md(None, "")
            return
        self.render_meta(d)
        detail.scroll_home(animate=False)
        cached, text = self.client.skill_md_cached(d)
        if cached:
            self.set_skill_md(d["skillUrl"], text)
        else:
            self.set_skill_md(d["skillUrl"], "*Loading SKILL.md…*", final=False)
            self._skill_md_timer = self.set_timer(
                SKILL_MD_DEBOUNCE, lambda: self.fetch_skill_md(d)
            )

    @work(thread=True, exclusive=True, group="skill_md")
    def fetch_skill_md(self, d: dict) -> None:
        text = self.client.skill_md(d)
        self.call_from_thread(self.set_skill_md, d["skillUrl"], text)

    def set_skill_md(self, url: str | None, text: str | None, *, final: bool = True) -> None:
        d = self.current()
        if url is not None and (d is None or d["skillUrl"] != url):
            return  # The highlight moved on.
        if d is not None and final and text and not d["description"]:
            # skills.sh search has no descriptions; SKILL.md's frontmatter does.
            self.render_meta(d)
        if final and text is None and d is not None:
            body = _missing_skill_md(d)
        else:
            body = strip_frontmatter(text or "")[:SKILL_MD_MAX_CHARS]
        key = f"{url}\0{body}"
        if key != self._shown_skill_md:
            self._shown_skill_md = key
            self.skill_md_view.update(body)

    @on(OptionList.OptionSelected, "#results")
    def selected(self) -> None:
        d = self.current()
        if d is None:
            return
        cached, text = self.client.skill_md_cached(d)
        if not cached:
            self.notify("SKILL.md is still loading.")
            return
        body = strip_frontmatter(text) if text else _missing_skill_md(d)
        description = core._frontmatter_description(text or "")
        self.push_screen(SkillScreen(d, body[:SKILL_MD_MAX_CHARS], description))

    # --- actions ---

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if isinstance(self.screen, SkillScreen) and action in self.MAIN_ACTIONS:
            return False
        if action == "focus_results" and self.focused is self.results:
            return False
        if action in ("hide", "open", "copy_url", "copy_install") and self.current() is None:
            return None  # Shown disabled in the footer.
        if action == "page":
            if self.loading_search is not None and parameters[0] > 0:
                return None
            if parameters[0] < 0 and self.search.page <= 1:
                return None
            if parameters[0] > 0 and not self.pagination.get("hasNext"):
                return None
        return True

    def action_focus_query(self) -> None:
        self.box.focus()

    def action_focus_results(self) -> None:
        self.results.focus()

    def action_page(self, delta: int) -> None:
        self.go(replace(self.search, page=self.search.page + delta))

    def action_toggle_sort(self) -> None:
        sorts = core.SORTS[self.search.source]
        sort = sorts[(sorts.index(self.search.sort) + 1) % len(sorts)]
        if self.home.source == self.search.source:
            self.home = replace(self.home, sort=sort)
        self.go(replace(self.search, sort=sort, page=1))

    def action_toggle_source(self) -> None:
        source = "skillsmp" if self.search.source == "skills.sh" else "skills.sh"
        sort = core.DEFAULT_SORT[source]
        # Picking a source by hand also means no falling back from it.
        self.home = replace(self.home, source=source, sort=sort, fallback=False)
        if self.search.query:
            self.go(replace(self.search, source=source, sort=sort, fallback=False, page=1))

    def action_hide(self, what: str) -> None:
        d = self.current()
        if d is None:
            return
        # The slash pins the term to the author/name label, so hiding the
        # author "react" doesn't also hide every skill that mentions React.
        term = f"{d['author']}/" if what == "author" else f"/{d['name']}"
        exclude = tuple(dict.fromkeys(self.search.exclude + (term,)))
        with self.box.prevent(Input.Changed):
            self.box.value = format_query(self.search.query, exclude)
        self.go(replace(self.search, exclude=exclude, page=1))
        self.notify(f"Hiding {term}  (edit the search box to undo)")

    def action_open(self, field: str) -> None:
        d = self.current()
        if d:
            open_skill_url(self, d, field)

    def action_copy_url(self) -> None:
        d = self.current()
        if d:
            copy_text(self, d["githubUrl"] or d["skillUrl"])

    def action_copy_install(self) -> None:
        d = self.current()
        if d:
            copy_text(self, d["installCommand"])

    def _notify_retry(self, message: str) -> None:
        self.call_from_thread(self.notify, message, severity="warning")


def run(args: dict) -> None:
    search = Search(
        query=args["query"],
        exclude=tuple(args["exclude"]),
        sort=args["sort"],
        page=args["page"],
        limit=args["limit"],
        category=args["category"],
        occupation=args["occupation"],
        language=args["language"],
        source=args["source"],
        fallback=args["fallback"],
    )
    SkillsmpApp(search).run()
