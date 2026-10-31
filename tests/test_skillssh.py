from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
from unittest import mock

import pytest

import skillsmp


def hit(skill_id: str, installs: int) -> dict:
    repo, _, slug = skill_id.rpartition("/")
    return {"id": skill_id, "source": repo, "skillId": slug, "name": slug, "installs": installs}


HITS = [
    hit("hashicorp/agent-skills/terraform-style-guide", 12774),
    hit("hashicorp/agent-skills/terraform-test", 8890),
    hit("acme/infra/terraform-native", 50000),
    hit("docs.stripe.com/stripe-docs", 20486),
]

SKILL_MD = "---\nname: terraform-test\ndescription: >\n  Write and run\n  Terraform tests.\n---\n# Terraform tests\n"

TREES = {
    "hashicorp/agent-skills": [
        {"path": "terraform/skills/terraform-style-guide/SKILL.md", "sha": "aaa"},
        {"path": "terraform/skills/terraform-test/SKILL.md", "sha": "bbb"},
        {"path": "terraform/skills/terraform-test/notes.md", "sha": "ccc"},
    ],
    # Renamed since skills.sh indexed it: only the contents still match.
    "acme/infra": [
        {"path": "skills/native/SKILL.md", "sha": skillsmp._git_blob_sha(SKILL_MD)},
        {"path": "skills/other/SKILL.md", "sha": "ddd"},
    ],
}

COMMITS = {
    ("hashicorp/agent-skills", "terraform/skills/terraform-style-guide"): "2026-08-10T15:37:46Z",
    ("hashicorp/agent-skills", "terraform/skills/terraform-test"): "2026-07-01T00:00:00Z",
    ("acme/infra", "skills/native"): "2025-01-02T03:04:05Z",
}

SKILLSMP_SKILL = {
    "name": "terraform-deploy",
    "author": "acme",
    "description": "Deploy with Terraform",
    "stars": 42,
    "updatedAt": 1700000000,
    "githubUrl": "https://github.com/acme/terraform-deploy",
    "skillUrl": "https://skillsmp.com/skills/terraform-deploy",
}


def response(body) -> mock.MagicMock:
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    r.headers.items.return_value = []
    return r


class Router:
    """Fake urlopen that answers by host and path."""

    def __init__(self, hits=HITS, skillssh_error: int | None = None, download=True, github_error=None):
        self.hits = hits
        self.skillssh_error = skillssh_error
        self.download = download
        self.github_error = github_error
        self.urls: list[str] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parts.query))
        if parts.netloc == "skills.sh":
            if self.skillssh_error:
                raise urllib.error.HTTPError(url, self.skillssh_error, "err", {}, io.BytesIO(b""))
            if parts.path == "/api/search":
                hits = self.hits
                if "." in params.get("owner", ""):
                    raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b"{}"))
                if "owner" in params:
                    hits = [h for h in hits if h["source"].split("/")[0] == params["owner"]]
                return response({"skills": hits})
            if parts.path.startswith("/api/download/") and self.download:
                return response({"hash": "x", "files": [{"path": "SKILL.md", "contents": SKILL_MD}]})
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        if parts.netloc == "skillsmp.com":
            return response({"data": {"skills": [SKILLSMP_SKILL], "pagination": {"total": 1, "page": 1, "totalPages": 1}}})
        if parts.netloc == "docs.stripe.com":
            if parts.path == "/.well-known/skills/stripe-docs/SKILL.md":
                return response(b"---\ndescription: >-\n  Stripe docs.\n---\n# Stripe\n")
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        if parts.netloc == "api.github.com":
            return self.github(req, parts)
        if parts.netloc == "raw.githubusercontent.com":
            return response(b"# from github\n")
        raise AssertionError(f"unexpected URL {url}")

    def github(self, req, parts):
        assert req.headers["Authorization"] == "Bearer gh-token"
        if self.github_error:
            raise urllib.error.HTTPError(req.full_url, self.github_error, "err", {}, io.BytesIO(b"{}"))
        tree_path = re.fullmatch(r"/repos/(.+)/git/trees/HEAD", parts.path)
        if tree_path:
            repo = tree_path.group(1)
            if repo not in TREES:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
            return response({"tree": [{"type": "blob", **e} for e in TREES[repo]], "truncated": False})
        assert parts.path == "/graphql"
        query = json.loads(req.data)["query"]
        data = {}
        for alias, owner, name, path in re.findall(
            r'(r\d+): repository\(owner: "(.*?)", name: "(.*?)"\) .*?history\(first: 1(?:, path: "(.*?)")?\)',
            query,
        ):
            date = COMMITS.get((f"{owner}/{name}", path))
            nodes = [{"committedDate": date}] if date else []
            data[alias] = {"defaultBranchRef": {"target": {"history": {"nodes": nodes}}}}
        return response({"data": data})

    def hosts(self) -> list[str]:
        return [urllib.parse.urlsplit(u).netloc for u in self.urls]


@pytest.fixture
def router():
    r = Router()
    with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
        yield r


def search(**kwargs):
    kwargs.setdefault("source", "skills.sh")
    skillsmp._cmd_search("terraform", **kwargs)


class TestSearch:
    def test_one_request_without_api_key(self, router, capsys):
        search()
        assert len(router.urls) == 1
        url = urllib.parse.urlsplit(router.urls[0])
        assert (url.netloc, url.path) == ("skills.sh", "/api/search")
        assert dict(urllib.parse.parse_qsl(url.query)) == {"q": "terraform", "limit": "200"}
        out = capsys.readouterr().out
        assert 'skills.sh search: "terraform" — 4 results (page 1/1)' in out
        assert "hashicorp/agent-skills/terraform-test  [8.9k installs]" in out
        assert "install: npx skills add hashicorp/agent-skills --skill terraform-test" in out
        assert "install: npx skills add https://docs.stripe.com --skill stripe-docs" in out

    def test_api_key_not_sent_to_skillssh(self, router):
        search()
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=router) as patched:
            search()
        assert "Authorization" not in patched.call_args.args[0].headers

    def test_pages_cut_locally(self, router, capsys):
        search(limit=3, page=2, output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert [s["id"] for s in data["skills"]] == ["docs.stripe.com/stripe-docs"]
        assert (data["page"], data["totalPages"], data["hasNext"]) == (2, 2, False)
        assert data["source"] == "skills.sh"
        assert len(router.urls) == 1

    def test_installs_sort(self, router, capsys):
        search(sort="installs", output_plain=True)
        lines = capsys.readouterr().out.splitlines()
        assert [line.split("\t")[1] for line in lines] == ["50000", "20486", "12774", "8890"]
        assert all(line.split("\t")[5:] == ["skills.sh", ""] for line in lines)

    def test_exclude_matches_repo_path(self, router, capsys):
        search(exclude=["hashicorp/"], output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["excluded"] == 2
        assert all(not s["id"].startswith("hashicorp/") for s in data["skills"])

    def test_full_result_set_marked_inexact(self, capsys):
        many = [hit(f"o/r/s{i}", i) for i in range(skillsmp.SKILLSSH_MAX_RESULTS)]
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=Router(hits=many)):
            search(output_json=True)
        assert json.loads(capsys.readouterr().out)["totalIsExact"] is False


class TestFallback:
    def test_no_results_falls_back_to_skillsmp(self, capsys):
        r = Router(hits=[])
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            search(fallback=True)
        captured = capsys.readouterr()
        assert r.hosts() == ["skills.sh", "skillsmp.com"]
        assert "no results on skills.sh; searching SkillsMP" in captured.err
        assert 'SkillsMP search: "terraform"' in captured.out
        assert "sortBy=stars" in r.urls[1]

    def test_error_falls_back_with_reason_in_json(self, capsys):
        r = Router(skillssh_error=503)
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r), mock.patch(
            "skillsmp.time.sleep"
        ):
            search(fallback=True, output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["source"] == "skillsmp"
        assert data["fallbackReason"].startswith("skills.sh failed (API error (503)")
        assert data["skills"][0]["name"] == "terraform-deploy"

    def test_explicit_source_does_not_fall_back(self, capsys):
        r = Router(hits=[])
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            search()
        assert r.hosts() == ["skills.sh"]
        assert "No results found" in capsys.readouterr().out

    def test_one_character_query_goes_straight_to_skillsmp(self, capsys):
        r = Router()
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            skillsmp._cmd_search("a", source="skills.sh", fallback=True, output_plain=True)
        assert r.hosts() == ["skillsmp.com"]

    def test_later_pages_do_not_fall_back(self, capsys):
        r = Router()
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            search(fallback=True, page=9)
        assert r.hosts() == ["skills.sh"]


class TestShow:
    def test_full_id_uses_skillssh(self, router, capsys):
        skillsmp._cmd_show("hashicorp/agent-skills/terraform-test", source="skills.sh")
        out = capsys.readouterr().out
        assert out.startswith("hashicorp/agent-skills/terraform-test  [8.9k installs]")
        assert "Write and run Terraform tests." in out
        assert "install:   npx skills add hashicorp/agent-skills --skill terraform-test" in out
        assert "--- SKILL.md ---\n" + SKILL_MD in out
        assert "skillsmp.com" not in " ".join(router.urls)
        assert "raw.githubusercontent.com" not in " ".join(router.urls)

    def test_site_hosted_skill_reads_well_known(self, router, capsys):
        skillsmp._cmd_show("docs.stripe.com/stripe-docs", source="skills.sh")
        out = capsys.readouterr().out
        assert out.startswith("docs.stripe.com/stripe-docs  [20.5k installs]")
        assert "Stripe docs." in out and "# Stripe" in out
        assert "install:   npx skills add https://docs.stripe.com --skill stripe-docs" in out
        assert router.urls[-2:] == [
            "https://docs.stripe.com/.well-known/agent-skills/stripe-docs/SKILL.md",
            "https://docs.stripe.com/.well-known/skills/stripe-docs/SKILL.md",
        ]

    def test_author_and_name_picks_most_installed(self, capsys):
        hits = [hit("acme/a/deploy", 5), hit("acme/b/deploy", 500)]
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=Router(hits=hits)):
            skillsmp._cmd_show("acme/deploy", source="skills.sh", output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "acme/b/deploy"
        assert data["otherMatches"] == ["https://skills.sh/acme/a/deploy"]
        assert data["skillMd"] == SKILL_MD

    def test_falls_back_to_skillsmp(self, capsys):
        r = Router(hits=[])
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            skillsmp._cmd_show("acme/terraform-deploy", source="skills.sh", fallback=True)
        assert r.hosts() == ["skills.sh", "skillsmp.com", "raw.githubusercontent.com"]
        assert "# from github" in capsys.readouterr().out

    def test_unknown_full_id_not_found(self, capsys):
        r = Router(hits=[], download=False)
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            with pytest.raises(SystemExit) as exc:
                skillsmp._cmd_show("x/y/z", source="skills.sh", fallback=True)
        assert exc.value.code == 1
        assert "skill not found: x/y/z" in capsys.readouterr().err
        assert "skillsmp.com" not in r.hosts()


class TestSourceSelection:
    @pytest.mark.parametrize(
        ("argv", "source", "fallback", "sort"),
        [
            (["q"], "skills.sh", True, "relevance"),
            (["--sort", "installs", "q"], "skills.sh", True, "installs"),
            (["--sort", "recent", "q"], "skillsmp", False, "recent"),
            (["--category", "devops", "q"], "skillsmp", False, "stars"),
            (["--source", "skills.sh", "q"], "skills.sh", False, "relevance"),
            (["--source", "skillsmp", "q"], "skillsmp", False, "stars"),
            (["show", "a/b/c"], "skills.sh", True, "relevance"),
        ],
    )
    def test_resolution(self, argv, source, fallback, sort):
        parsed = skillsmp._parse_args(argv)
        assert (parsed["source"], parsed["fallback"], parsed["sort"]) == (source, fallback, sort)

    @pytest.mark.parametrize(
        ("argv", "needle"),
        [
            (["--source", "github", "q"], "--source must be"),
            (["--source", "skills.sh", "--language", "en", "q"], "--language needs --source skillsmp"),
            (["--source", "skills.sh", "--sort", "stars", "q"], "--sort stars needs --source skillsmp"),
            (["--source", "skillsmp", "--sort", "installs", "q"], "--sort installs needs --source skills.sh"),
            (["show", "--source", "skillsmp", "a/b/c"], "skills.sh ID"),
            (["show", "a/b/c/d"], "<owner>/<repo>/<skill>"),
            (["categories", "--source", "skillsmp"], "--source does not apply"),
        ],
    )
    def test_errors(self, capsys, argv, needle):
        with pytest.raises(SystemExit) as exc:
            skillsmp._parse_args(argv)
        assert exc.value.code == 2
        assert needle in capsys.readouterr().err


class TestHelpers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("---\nname: x\ndescription: Plain text\n---\nbody", "Plain text"),
            ('---\ndescription: "Quoted: yes"\n---\n', "Quoted: yes"),
            ("---\ndescription: |\n  one\n  two\nname: x\n---\n", "one two"),
            ("# no frontmatter\ndescription: nope\n", ""),
        ],
    )
    def test_frontmatter_description(self, text, expected):
        assert skillsmp._frontmatter_description(text) == expected

    def test_skillsmp_install_command_uses_github_url(self):
        d = skillsmp._normalize_skill(SKILLSMP_SKILL)
        assert d["installCommand"] == "npx skills add https://github.com/acme/terraform-deploy"
        assert d["id"] == "acme/terraform-deploy"
        assert d["registry"] == "skillsmp"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(skillsmp, "_github_token", lambda: "gh-token")


def github_urls(router) -> list[str]:
    return [u.removeprefix("https://api.github.com") for u in router.urls if "api.github.com" in u]


class TestCommitDates:
    def test_search_shows_last_commit_of_skill_folder(self, router, token, capsys):
        search(output_json=True)
        dates = {s["id"]: s["updatedAt"] for s in json.loads(capsys.readouterr().out)["skills"]}
        assert dates == {
            "hashicorp/agent-skills/terraform-style-guide": "2026-08-10T15:37:46Z",
            "hashicorp/agent-skills/terraform-test": "2026-07-01T00:00:00Z",
            "acme/infra/terraform-native": "2025-01-02T03:04:05Z",
            "docs.stripe.com/stripe-docs": None,  # not on GitHub
        }

    def test_human_and_plain_output(self, router, token, capsys):
        search()
        assert "terraform-test  [8.9k installs, updated 2026-07-01]" in capsys.readouterr().out
        search(output_plain=True)
        line = capsys.readouterr().out.splitlines()[0]
        assert line.split("\t")[-1] == "2026-08-10"

    def test_folders_cached_between_runs(self, router, token, capsys):
        search()
        first = github_urls(router)
        assert sorted(u for u in first if "/trees/" in u) == [
            "/repos/acme/infra/git/trees/HEAD?recursive=1",
            "/repos/hashicorp/agent-skills/git/trees/HEAD?recursive=1",
        ]
        skillsmp._skill_paths.entries = None  # as if it were a new process
        router.urls.clear()
        search()
        assert github_urls(router) == ["/graphql"]

    def test_lookups_split_into_small_queries(self, token, capsys, monkeypatch):
        monkeypatch.setattr(skillsmp, "GRAPHQL_BATCH", 2)
        r = Router()
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            search(output_json=True)
        assert github_urls(r).count("/graphql") == 2  # 3 folders
        assert json.loads(capsys.readouterr().out)["skills"][2]["updatedAt"] == "2025-01-02T03:04:05Z"

    def test_deleted_repo_remembered_as_unknown(self, token, capsys):
        r = Router(hits=[hit("gone/repo/thing", 5)])
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            search()
            search()
        assert github_urls(r) == ["/repos/gone/repo/git/trees/HEAD?recursive=1"]
        assert "[5 installs, GitHub repo deleted]" in capsys.readouterr().out
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r):
            search(output_json=True)
        skill = json.loads(capsys.readouterr().out)["skills"][0]
        assert (skill["updatedAt"], skill["repoMissing"]) == (None, True)

    def test_github_failure_still_prints_results(self, token, capsys):
        r = Router(github_error=500)
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=r), mock.patch(
            "skillsmp.time.sleep"
        ):
            search()
        captured = capsys.readouterr()
        assert "terraform-test  [8.9k installs]" in captured.out
        assert "skills.sh search" in captured.out

    def test_no_token_warns_without_calling_github(self, router, capsys):
        search()
        assert "last-commit dates need a GitHub token" in capsys.readouterr().err
        assert github_urls(router) == []

    def test_show_includes_date(self, router, token, capsys):
        skillsmp._cmd_show("hashicorp/agent-skills/terraform-test", source="skills.sh", output_json=True)
        assert json.loads(capsys.readouterr().out)["updatedAt"] == "2026-07-01T00:00:00Z"


class TestPickSkillDir:
    DIRS = {"a/deploy": "s1", "b/deploy": "s1", "deploy": "s2", "x/y/other": "s3"}

    def test_contents_beat_names(self):
        md = "same"
        dirs = {"skills/renamed": skillsmp._git_blob_sha(md), "skills/deploy": "zzz"}
        assert skillsmp._pick_skill_dir(dirs, "deploy", md) == "skills/renamed"

    def test_shallowest_name_match(self):
        assert skillsmp._pick_skill_dir(self.DIRS, "Deploy", None) == "deploy"

    def test_only_skill_in_repo(self):
        assert skillsmp._pick_skill_dir({"": "s"}, "whatever", None) == ""

    def test_root_is_named_after_repo(self):
        dirs = {"": "s1", "subskills/a": "s2", "subskills/b": "s3"}
        assert skillsmp._pick_skill_dir(dirs, "prose", None, "prose") == ""
        assert skillsmp._pick_skill_dir(dirs, "prose", None, "other") is None

    def test_no_match(self):
        assert skillsmp._pick_skill_dir(self.DIRS, "missing", None) is None

    def test_blob_sha_matches_git(self):
        # `printf 'hello\n' | git hash-object --stdin`
        assert skillsmp._git_blob_sha("hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
