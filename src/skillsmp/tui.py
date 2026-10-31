"""Full-screen browser for SkillsMP search results (`skillsmp -i`)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, replace

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
# Textual's Markdown widget gets sluggish on huge documents.
SKILL_MD_MAX_CHARS = 60_000


@dataclass(frozen=True)
class Search:
    query: str
    exclude: tuple[str, ...] = ()
    sort: str = "stars"
    page: int = 1
    limit: int = 20
    category: str | None = None
    occupation: str | None = None
    language: str | None = None

    def params(self) -> dict:
        return {
            "q": self.query,
            "sortBy": self.sort,
            "category": self.category,
            "occupation": self.occupation,
            "language": self.language,
        }


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
    """Caching, thread-safe access to the search API and SKILL.md files.

    Raw API pages are cached rather than result pages, so changing exclusions
    or paging back re-filters what's already here without a request.
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

    @staticmethod
    def _key(params: dict) -> tuple:
        return tuple(sorted((k, str(v)) for k, v in params.items() if v is not None))

    def _request_json(self, params: dict) -> dict:
        key = self._key(params)
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
                req = core._api_url_request("search", params)
                try:
                    raw = core._fetch(req, on_retry=self.on_retry, on_headers=self._track_quota)
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

    def can_prefetch(self) -> bool:
        if self.exhausted:
            return False
        return self.quota is None or self.quota[0] > PREFETCH_MIN_REMAINING

    def _cached_json(self, params: dict) -> dict:
        with self._lock:
            data = self._raw.get(self._key(params))
        if data is None:
            raise _NotCached
        return data

    def _run(self, search: Search, request_json):
        # Always read the API 50 at a time and cut display pages from that:
        # one request covers a few pages, and exclusions just re-filter it.
        needed = -(-search.page * search.limit // core.MAX_LIMIT)
        return core._search(
            search.params(),
            list(search.exclude),
            limit=search.limit,
            page=search.page,
            request_json=request_json,
            stream=True,
            max_api_pages=needed + core.EXCLUDE_MAX_REQUESTS,
        )

    def search(self, search: Search):
        """Blocking search; raises core.FetchError."""
        return self._run(search, self._request_json)

    def search_cached(self, search: Search):
        """The result if every page it needs is cached, else None."""
        try:
            return self._run(search, self._cached_json)
        except _NotCached:
            return None

    def skill_md(self, github_url: str) -> str | None:
        with self._lock:
            if github_url in self._skill_md:
                return self._skill_md[github_url]
        text = core._fetch_skill_md(github_url)
        with self._lock:
            self._skill_md[github_url] = text
        return text

    def skill_md_cached(self, github_url: str) -> tuple[bool, str | None]:
        with self._lock:
            return github_url in self._skill_md, self._skill_md.get(github_url)


def _detail_text(d: dict) -> Text:
    t = Text()
    t.append(f"{d['author']}/", style="dim")
    t.append(d["name"], style="bold")
    t.append("\n")
    meta = [f"★ {core._format_stars(d['stars'])}", f"updated {core._format_timestamp(d['updatedAt'])}"]
    if d["contentLanguage"]:
        meta.append(f"lang {d['contentLanguage']}")
    t.append("  ·  ".join(meta), style="dim")
    t.append("\n\n")
    if d["description"]:
        t.append(" ".join(d["description"].split()))
        t.append("\n\n")
    for label, url in (("github  ", d["githubUrl"]), ("skillsmp", d["skillUrl"])):
        if url:
            t.append(f"{label} ", style="dim")
            t.append(url, style="underline")
            t.append("\n")
    return t


def _option(d: dict) -> Option:
    name = Text(no_wrap=True, overflow="ellipsis")
    name.append(f"{d['author']}/", style="dim")
    name.append(d["name"], style="bold")
    if d["contentLanguage"] and d["contentLanguage"] != "en":
        name.append(f" {d['contentLanguage']}", style="dim italic")
    row = Table.grid(expand=True, padding=(0, 1))
    row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    row.add_column(justify="right", no_wrap=True)
    row.add_row(name, Text(f"★ {core._format_stars(d['stars'])}", style="dim"))
    return Option(row)


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
        Binding("w", "open('skillUrl')", "SkillsMP"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("j", "scroll('down')", show=False),
        Binding("k", "scroll('up')", show=False),
    ]

    def __init__(self, skill: dict, markdown: str) -> None:
        super().__init__()
        self.skill = skill
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="reader"):
            yield Static(_detail_text(self.skill), id="reader-meta")
            yield Markdown(self.markdown)
        yield Footer()

    def on_mount(self) -> None:
        reader = self.query_one("#reader")
        reader.border_title = f"{self.skill['author']}/{self.skill['name']}"
        reader.focus()

    def action_scroll(self, direction: str) -> None:
        reader = self.query_one("#reader", VerticalScroll)
        (reader.scroll_down if direction == "down" else reader.scroll_up)()

    def action_open(self, field: str) -> None:
        open_skill_url(self.app, self.skill, field)

    def action_copy_url(self) -> None:
        copy_skill_url(self.app, self.skill)


def open_skill_url(app: App, d: dict, field: str) -> None:
    if d[field]:
        app.open_url(d[field])


def copy_skill_url(app: App, d: dict) -> None:
    url = d["githubUrl"] or d["skillUrl"]
    app.copy_to_clipboard(url)  # OSC 52, which works over ssh
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=url.encode(), check=False)
    app.notify(f"Copied {url}")


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
        Binding("right,]", "page(1)", "Next page", key_display="→"),
        Binding("left,[", "page(-1)", "Prev page", key_display="←"),
        Binding("s", "toggle_sort", "Sort"),
        Binding("x", "hide('name')", "Hide name"),
        Binding("X", "hide('author')", "Hide author"),
        Binding("o", "open('githubUrl')", "GitHub"),
        Binding("w", "open('skillUrl')", "SkillsMP", show=False),
        Binding("y", "copy_url", "Copy URL"),
        Binding("q", "quit", "Quit"),
    ]

    # Actions that act on the results list, so are off while the reader is up.
    MAIN_ACTIONS = {
        "focus_query", "focus_results", "page", "toggle_sort", "hide", "open", "copy_url",
    }

    def __init__(self, search: Search, client: Client | None = None) -> None:
        super().__init__()
        # Start in the results when there's a query, else in the search box.
        self.AUTO_FOCUS = "#results" if search.query else "#query"
        self.search = search
        self.client = client or Client(on_retry=self._notify_retry)
        self.skills: list[dict] = []
        self.pagination: dict = {}
        self.excluded = 0
        self.loading_search: Search | None = None
        self._search_timer: Timer | None = None
        self._skill_md_timer: Timer | None = None
        self._shown_skill_md: str | None = None

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
            self.show_results(search, cached)
            return
        self.loading_search = search
        self.results.add_class("stale")
        self.set_status(f"Searching “{search.query}”…")
        self.refresh_bindings()
        self.fetch(search)

    @work(thread=True, exclusive=True, group="search")
    def fetch(self, search: Search) -> None:
        try:
            result = self.client.search(search)
        except core.FetchError as e:
            self.call_from_thread(self.show_error, search, str(e))
            return
        self.call_from_thread(self.show_results, search, result)

    @work(thread=True, group="prefetch")
    def prefetch(self, search: Search) -> None:
        try:
            self.client.search(search)
        except core.FetchError:
            pass  # The page will be fetched (and the error shown) if visited.

    def show_results(self, search: Search, result) -> None:
        if search != self.search:
            return  # A newer search has replaced this one.
        self.loading_search = None
        self.skills, self.pagination, self.excluded = result
        self.results.remove_class("stale")
        self.results.set_options([_option(core._normalize_skill(s)) for s in self.skills])
        if self.skills:
            self.results.highlighted = 0
        self.show_detail(self.current())
        self.set_status()
        self.refresh_bindings()
        following = replace(search, page=search.page + 1)
        if (
            self.pagination.get("hasNext")
            and self.client.search_cached(following) is None
            and self.client.can_prefetch()
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
            if not self.skills:
                parts = [f"No results for “{s.query}”"]
            elif self.pagination.get("totalIsExact", True):
                total = self.pagination.get("total", len(self.skills))
                pages = self.pagination.get("totalPages", 1)
                parts = [f"{total} results", f"page {s.page}/{pages}"]
            else:
                more = "more →" if self.pagination.get("hasNext") else "last page"
                parts = [f"page {s.page}", more]
            parts.append(f"sort: {s.sort}")
            if self.excluded:
                parts.append(f"{self.excluded} hidden")
            if self.client.quota:
                remaining, limit, period = self.client.quota
                parts.append(f"{remaining}/{limit} searches left {period}")
            filters = [
                f"{k} {v}"
                for k, v in (("category", s.category), ("occupation", s.occupation), ("language", s.language))
                if v
            ]
            message = "  ·  ".join(parts + filters)
        status.update(message)

    # --- query box ---

    def search_from_box(self) -> Search | None:
        query, exclude = parse_query(self.box.value)
        if not query:
            return None
        return replace(self.search, query=query, exclude=exclude, page=1)

    @on(Input.Changed, "#query")
    def query_changed(self) -> None:
        if self._search_timer:
            self._search_timer.stop()
        self._search_timer = self.set_timer(SEARCH_DEBOUNCE, self.search_if_changed)

    def search_if_changed(self) -> None:
        """Apply typing when it costs no request (e.g. editing exclusions)."""
        search = self.search_from_box()
        if search is None or (search.query, search.exclude) == (self.search.query, self.search.exclude):
            return
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

    def current(self) -> dict | None:
        index = self.results.highlighted
        if index is None or index >= len(self.skills):
            return None
        return core._normalize_skill(self.skills[index])

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
        meta.update(_detail_text(d))
        detail.scroll_home(animate=False)
        url = d["githubUrl"]
        cached, text = self.client.skill_md_cached(url)
        if cached:
            self.set_skill_md(url, text)
        else:
            self.set_skill_md(url, "*Loading SKILL.md…*", final=False)
            self._skill_md_timer = self.set_timer(
                SKILL_MD_DEBOUNCE, lambda: self.fetch_skill_md(url)
            )

    @work(thread=True, exclusive=True, group="skill_md")
    def fetch_skill_md(self, url: str) -> None:
        text = self.client.skill_md(url)
        self.call_from_thread(self.set_skill_md, url, text)

    def set_skill_md(self, url: str | None, text: str | None, *, final: bool = True) -> None:
        d = self.current()
        if url is not None and (d is None or d["githubUrl"] != url):
            return  # The highlight moved on.
        if final and text is None:
            body = "*Couldn't fetch SKILL.md from GitHub.*"
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
        cached, text = self.client.skill_md_cached(d["githubUrl"])
        if not cached:
            self.notify("SKILL.md is still loading.")
            return
        body = strip_frontmatter(text) if text else "*Couldn't fetch SKILL.md from GitHub.*"
        self.push_screen(SkillScreen(d, body[:SKILL_MD_MAX_CHARS]))

    # --- actions ---

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if isinstance(self.screen, SkillScreen) and action in self.MAIN_ACTIONS:
            return False
        if action == "focus_results" and self.focused is self.results:
            return False
        if action in ("hide", "open", "copy_url") and self.current() is None:
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
        sort = "recent" if self.search.sort == "stars" else "stars"
        self.go(replace(self.search, sort=sort, page=1))

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
            copy_skill_url(self, d)

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
    )
    SkillsmpApp(search).run()
