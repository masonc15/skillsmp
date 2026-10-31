from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
from unittest import mock

import pytest

import skillsmp
from skillsmp import tui
from skillsmp.tui import Client, Search, SkillScreen, SkillsmpApp

SKILLS = [
    {
        "name": f"native-{i}" if i % 3 == 0 else f"skill-{i}",
        "author": "acme" if i % 2 else "other",
        "description": f"Skill number {i}",
        "stars": 100 - i,
        "updatedAt": 1700000000,
        "githubUrl": f"https://github.com/acme/repo/tree/main/{i}",
        "skillUrl": f"https://skillsmp.com/skills/{i}",
    }
    for i in range(120)
]


class FakeAPI:
    """Stands in for skillsmp._fetch, serving SKILLS in pages."""

    def __init__(self, delay: float = 0.0, fail: bool = False, remaining: int = 50) -> None:
        self.calls: list[dict] = []
        self.delay = delay
        self.fail = fail
        self.remaining = remaining
        self.lock = threading.Lock()

    def __call__(self, req, on_retry=None, on_headers=None) -> bytes:
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        with self.lock:
            self.calls.append(params)
            self.remaining = max(self.remaining - 1, -1)
            remaining = self.remaining
        time.sleep(self.delay)
        if self.fail:
            raise skillsmp.FetchError("API error (500): broken", {"error": "broken"})
        if remaining < 0:
            if on_headers:
                on_headers({"X-RateLimit-Daily-Limit": "50", "X-RateLimit-Daily-Remaining": "0"})
            raise skillsmp.FetchError(
                "SkillsMP search quota used up (allowance exhausted); it resets in 23h 6m",
                {"error": "allowance exhausted", "code": 429, "errorCode": "DAILY_QUOTA_EXCEEDED"},
            )
        if on_headers:
            on_headers({"X-RateLimit-Daily-Limit": "50", "X-RateLimit-Daily-Remaining": str(remaining)})
        limit, page = int(params["limit"]), int(params["page"])
        skills = SKILLS if params.get("sortBy") != "recent" else SKILLS[::-1]
        chunk = skills[(page - 1) * limit : page * limit]
        has_next = page * limit < len(skills)
        body = {"data": {"skills": chunk, "pagination": {"page": page, "hasNext": has_next, "totalIsExact": False}}}
        return json.dumps(body).encode()


@pytest.fixture
def api():
    fake = FakeAPI()
    with mock.patch("skillsmp._fetch", fake), mock.patch(
        "skillsmp._fetch_skill_md", lambda url: f"---\nname: x\n---\n# Doc for {url}\n"
    ):
        yield fake


def run_app(search: Search, script):
    async def main():
        app = SkillsmpApp(search)
        async with app.run_test(size=(140, 40)) as pilot:
            await script(app, pilot)

    asyncio.run(main())


async def until(pilot, cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await pilot.pause(0.02)


def names(app) -> list[str]:
    return [s["name"] for s in app.skills]


class TestQuerySyntax:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("react testing", ("react testing", ())),
            ("react -native -acme/", ("react", ("native", "acme/"))),
            ("react -'react native' -x -x", ("react", ("react native", "x"))),
            ("react -", ("react -", ())),
            ('react "unclosed -x', ('react "unclosed', ("x",))),
        ],
    )
    def test_parse_query(self, text, expected):
        assert tui.parse_query(text) == expected

    def test_format_round_trips(self):
        exclude = ("native", "react native", "acme/")
        text = tui.format_query("react", exclude)
        assert text == "react -native -'react native' -acme/"
        assert tui.parse_query(text) == ("react", exclude)

    def test_strip_frontmatter(self):
        assert tui.strip_frontmatter("---\nname: x\n---\n\n# Hi\n") == "# Hi\n"
        assert tui.strip_frontmatter("# No frontmatter\n") == "# No frontmatter\n"


class TestClient:
    def test_search_caches_raw_pages(self, api):
        client = Client()
        search = Search("q", limit=10)
        assert client.search_cached(search) is None
        skills, pagination, excluded = client.search(search)
        assert len(skills) == 10 and pagination["hasNext"] and excluded == 0
        assert client.search_cached(search) == (skills, pagination, excluded)
        assert len(api.calls) == 1

    def test_changing_exclusions_reuses_cached_pages(self, api):
        client = Client()
        client.search(Search("q", exclude=("native",), limit=10))
        calls = len(api.calls)
        skills, _, excluded = client.search_cached(Search("q", exclude=("native", "acme/"), limit=10))
        assert len(api.calls) == calls
        assert excluded > 0
        assert all("native" not in s["name"] and s["author"] != "acme" for s in skills)

    def test_concurrent_requests_for_one_page_fetch_once(self, api):
        api.delay = 0.2
        client = Client()
        threads = [threading.Thread(target=client.search, args=(Search("q"),)) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(api.calls) == 1


class TestApp:
    def test_loads_results_into_focused_list(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            assert app.focused is app.results
            assert app.results.option_count == 20
            assert app.current()["name"] == "native-0"
            assert "page 1" in str(app.status.render())

        run_app(Search("q"), script)

    def test_pages_come_from_fifty_result_requests(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            await pilot.pause(0.2)
            # One request of 50 covers pages 1 and 2, so nothing to prefetch.
            assert [c["limit"] for c in api.calls] == ["50"]
            await pilot.press("right")
            assert app.search.page == 2
            assert names(app)[0] == "skill-20"
            # Page 3 needs results 41-60, so now the second request goes out.
            await until(pilot, lambda: len(api.calls) == 2)
            assert api.calls[1]["page"] == "2"
            await pilot.press("right")
            assert names(app)[0] == "skill-40"
            assert app.loading_search is None
            await pilot.press("left")
            await pilot.press("left")
            assert names(app)[0] == "native-0"
            assert len(api.calls) == 2

        run_app(Search("q"), script)

    def test_prefetch_stops_when_quota_runs_low(self, api):
        api.remaining = tui.PREFETCH_MIN_REMAINING + 1

        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            await pilot.press("right")
            await pilot.pause(0.3)
            assert len(api.calls) == 1
            assert app.client.quota == (tui.PREFETCH_MIN_REMAINING, 50, "today")
            assert "10/50 searches left today" in str(app.status.render())

        run_app(Search("q"), script)

    def test_exhausted_quota_explains_reset(self, api):
        api.remaining = 0

        async def script(app, pilot):
            await until(pilot, lambda: app.loading_search is None)
            status = str(app.status.render())
            assert "quota used up" in status and "resets in 23h 6m" in status
            assert app.status.has_class("error")
            assert app.client.exhausted

        run_app(Search("q"), script)

    def test_typing_exclusions_refilters_without_requests(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            app.box.focus()
            await pilot.press("end")
            for key in ("space", "minus", "n", "a", "t"):
                await pilot.press(key)
            await until(pilot, lambda: app.search.exclude == ("nat",))
            assert app.loading_search is None  # served from the cache
            assert all("native" not in n for n in names(app))
            # Any further request is the background prefetch for page 2.
            assert [c["page"] for c in api.calls[1:]] in ([], ["2"])

        run_app(Search("q"), script)

    def test_typing_a_new_query_waits_for_enter(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            app.box.focus()
            for key in ("r", "e", "a", "c", "t"):
                await pilot.press(key)
            await pilot.pause(tui.SEARCH_DEBOUNCE + 0.2)
            assert len(api.calls) == 1
            assert "Press Enter" in str(app.status.render())
            await pilot.press("enter")
            await until(pilot, lambda: app.search.query == "react" and app.loading_search is None)
            assert len(api.calls) == 2

        run_app(Search("q"), script)

    def test_prev_page_disabled_on_first_page(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            await pilot.press("left")
            assert app.search.page == 1

        run_app(Search("q"), script)

    def test_hide_keys_update_search_box(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            await pilot.press("x")  # highlighted: other/native-0
            await until(pilot, lambda: app.loading_search is None and app.search.exclude)
            assert app.box.value == "q -/native-0"
            assert "native-0" not in names(app)
            await pilot.press("X")  # the top row is now acme/skill-1
            await until(pilot, lambda: len(app.search.exclude) == 2 and app.loading_search is None)
            assert app.box.value == "q -/native-0 -acme/"
            assert app.skills and all(s["author"] == "other" for s in app.skills)
            assert "hidden" in str(app.status.render())

        run_app(Search("q"), script)

    def test_typing_in_search_box_does_not_fire_bindings(self, api):
        async def script(app, pilot):
            await pilot.press("slash")
            assert app.focused is app.box
            for key in ("s", "x", "q", "space", "minus", "y"):
                await pilot.press(key)
            assert app.box.value.endswith("sxq -y")
            assert app.search.sort == "stars"
            await pilot.press("enter")
            await until(pilot, lambda: app.search.exclude == ("y",))
            # Focusing the box selects its text, so typing replaced the query.
            assert app.search.query == "sxq"
            assert app.focused is app.results

        run_app(Search("q"), script)

    def test_sort_toggle(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            await pilot.press("s")
            await until(pilot, lambda: app.search.sort == "recent" and app.loading_search is None)
            assert names(app)[0] == "skill-119"

        run_app(Search("q"), script)

    def test_reader_opens_and_closes(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            url = app.current()["githubUrl"]
            await until(pilot, lambda: app.client.skill_md_cached(url)[0])
            await pilot.press("enter")
            assert isinstance(app.screen, SkillScreen)
            assert app.screen.markdown == f"# Doc for {url}\n"
            await pilot.press("x")  # main-screen keys are off in the reader
            assert app.search.exclude == ()
            await pilot.press("escape")
            assert not isinstance(app.screen, SkillScreen)

        run_app(Search("q"), script)

    def test_open_and_copy_use_selected_skill(self, api):
        async def script(app, pilot):
            await until(pilot, lambda: app.skills)
            with mock.patch.object(app, "open_url") as open_url, mock.patch.object(
                tui.shutil, "which", return_value=None
            ):
                await pilot.press("o")
                await pilot.press("y")
            open_url.assert_called_once_with(SKILLS[0]["githubUrl"])
            assert app.clipboard == SKILLS[0]["githubUrl"]

        run_app(Search("q"), script)

    def test_fetch_error_shown_in_status(self, api):
        api.fail = True

        async def script(app, pilot):
            await until(pilot, lambda: app.loading_search is None)
            assert "broken" in str(app.status.render())
            assert app.status.has_class("error")

        run_app(Search("q"), script)

    def test_empty_query_starts_in_search_box(self, api):
        async def script(app, pilot):
            await pilot.pause()
            assert app.focused is app.box
            assert api.calls == []

        run_app(Search(""), script)


class TestInteractiveFlag:
    @pytest.fixture
    def tty(self, monkeypatch):
        monkeypatch.setattr(skillsmp, "_isatty", lambda stream: True)

    def test_parses_with_and_without_query(self, tty):
        parsed = skillsmp._parse_args(["-i"])
        assert parsed["mode"] == "interactive"
        assert parsed["query"] == ""
        assert parsed["limit"] == 20
        parsed = skillsmp._parse_args(["-i", "-x", "native", "-n", "5", "react"])
        assert (parsed["query"], parsed["exclude"], parsed["limit"]) == ("react", ["native"], 5)

    @pytest.mark.parametrize(
        "argv", [["-i", "--json", "q"], ["-i", "--plain", "q"], ["show", "-i", "a/b"]]
    )
    def test_usage_errors(self, tty, argv):
        with pytest.raises(SystemExit) as exc:
            skillsmp._parse_args(argv)
        assert exc.value.code == 2

    def test_requires_terminal(self, capsys):
        with pytest.raises(SystemExit) as exc:
            skillsmp._parse_args(["-i", "q"])
        assert exc.value.code == 2
        assert "needs a terminal" in capsys.readouterr().err

    def test_run_hands_parsed_search_to_tui(self, tty):
        argv = ["skillsmp", "-i", "-s", "recent", "-x", "native", "--language", "en", "react"]
        with mock.patch("sys.argv", argv), mock.patch.object(tui, "SkillsmpApp") as app_cls:
            skillsmp.main()
        app_cls.assert_called_once_with(
            Search("react", exclude=("native",), sort="recent", limit=20, language="en")
        )
        app_cls.return_value.run.assert_called_once_with()
