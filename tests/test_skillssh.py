from __future__ import annotations

import io
import json
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

    def __init__(self, hits=HITS, skillssh_error: int | None = None, download=True):
        self.hits = hits
        self.skillssh_error = skillssh_error
        self.download = download
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
        if parts.netloc == "raw.githubusercontent.com":
            return response(b"# from github\n")
        raise AssertionError(f"unexpected URL {url}")

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
        assert all(line.endswith("\tskills.sh") for line in lines)

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
