"""Conventional test suite for the skillsmp CLI."""

from __future__ import annotations

import json
import sys
import textwrap
import urllib.error
from unittest import mock

import pytest
from conftest import FAKE_API_KEY, make_http_error, make_raw_response, make_response

import skillsmp


def parse_args(argv: list[str]) -> dict:
    return skillsmp._parse_args(argv)


def assert_exit_code(exc: pytest.ExceptionInfo[SystemExit], code: int) -> None:
    assert exc.value.code == code


class TestArgumentParsing:
    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["terraform"], {"mode": "search", "query": "terraform"}),
            (["react", "testing", "library"], {"query": "react testing library"}),
            (["--ai", "optimize queries"], {"mode": "ai"}),
            (["-a", "optimize queries"], {"mode": "ai"}),
            (["--json", "q"], {"json": True}),
            (["-j", "q"], {"json": True}),
            (["--plain", "q"], {"plain": True}),
            (["--limit", "25", "q"], {"limit": 25}),
            (["-n", "5", "q"], {"limit": 5}),
            (["--page", "3", "q"], {"page": 3}),
            (["-p", "3", "q"], {"page": 3}),
            (["--sort", "recent", "q"], {"sort": "recent"}),
            (["-s", "stars", "q"], {"sort": "stars"}),
        ],
    )
    def test_supported_flags_and_modes(self, argv, expected):
        parsed = parse_args(argv)
        for key, value in expected.items():
            assert parsed[key] == value

    def test_defaults_when_flags_omitted(self):
        parsed = parse_args(["q"])
        assert parsed["limit"] == 10
        assert parsed["page"] == 1
        assert parsed["sort"] == "stars"
        assert parsed["json"] is False
        assert parsed["plain"] is False

    def test_flags_stop_after_first_positional(self):
        parsed = parse_args(["hello", "--ai", "--json"])
        assert parsed["mode"] == "search"
        assert parsed["json"] is False
        assert parsed["query"] == "hello --ai --json"

    def test_double_dash_ends_flag_parsing(self):
        parsed = parse_args(["--", "--ai", "-j"])
        assert parsed["mode"] == "search"
        assert parsed["query"] == "--ai -j"

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["--unknown", "q"],
            ["--json", "--plain", "q"],
            ["--limit", "abc", "q"],
            ["--limit", "0", "q"],
            ["--limit", "101", "q"],
            ["--limit", "999999", "q"],
            ["--page", "abc", "q"],
            ["--sort", "name", "q"],
            ["--limit"],
            ["--page"],
            ["--sort"],
            ["--ai", "--limit", "5", "q"],
            ["--ai", "--page", "2", "q"],
            ["--ai", "--sort", "recent", "q"],
            ["--limit", "5"],
        ],
    )
    def test_usage_errors_exit_2(self, argv):
        with pytest.raises(SystemExit) as exc:
            parse_args(argv)
        assert_exit_code(exc, 2)

    def test_limit_range_boundaries(self):
        assert parse_args(["--limit", "1", "q"])["limit"] == 1
        assert parse_args(["--limit", "100", "q"])["limit"] == 100

    def test_category_and_occupation_flags(self):
        parsed = parse_args(["--category", "devops", "--occupation", "lawyers", "q"])
        assert parsed["category"] == "devops"
        assert parsed["occupation"] == "lawyers"

    def test_category_and_occupation_default_to_none(self):
        parsed = parse_args(["q"])
        assert parsed["category"] is None
        assert parsed["occupation"] is None


class TestSubcommandParsing:
    @pytest.mark.parametrize(
        ("argv", "mode", "query"),
        [
            (["categories"], "categories", ""),
            (["occupations"], "occupations", ""),
            (["occupations", "--json"], "occupations", ""),
            (["show", "acme/deploy"], "show", "acme/deploy"),
            (["show", "acme/deploy", "--json"], "show", "acme/deploy"),
        ],
    )
    def test_subcommand_modes(self, argv, mode, query):
        parsed = parse_args(argv)
        assert parsed["mode"] == mode
        assert parsed["query"] == query

    def test_double_dash_escapes_subcommand_names(self):
        parsed = parse_args(["--", "categories"])
        assert parsed["mode"] == "search"
        assert parsed["query"] == "categories"

    @pytest.mark.parametrize(
        "argv",
        [
            ["show"],
            ["show", "no-slash"],
            ["show", "acme/"],
            ["show", "/deploy"],
            ["show", "acme/deploy", "extra"],
            ["show", "acme/deploy", "--plain"],
            ["categories", "extra"],
            ["occupations", "extra"],
            ["categories", "--ai"],
            ["show", "acme/deploy", "--limit", "5"],
            ["categories", "--sort", "recent"],
            ["--ai", "--category", "devops", "q"],
            ["--category"],
            ["--occupation"],
        ],
    )
    def test_subcommand_usage_errors_exit_2(self, argv):
        with pytest.raises(SystemExit) as exc:
            parse_args(argv)
        assert_exit_code(exc, 2)


class TestHelpAndVersion:
    def test_no_args_prints_concise_help_to_stderr(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_args([])
        assert_exit_code(exc, 2)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "skillsmp" in captured.err
        assert "Run \"skillsmp --help\" for all options." in captured.err

    def test_help_prints_full_help_to_stdout(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_args(["--help"])
        assert_exit_code(exc, 0)
        captured = capsys.readouterr()
        assert "Usage:" in captured.out
        assert "Flags:" in captured.out
        assert captured.err == ""

    def test_version_prints_to_stdout(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_args(["--version"])
        assert_exit_code(exc, 0)
        captured = capsys.readouterr()
        assert captured.out.strip() == f"skillsmp {skillsmp.__version__}"
        assert captured.err == ""

    def test_full_help_uses_ansi_bold_when_tty(self, capsys):
        with mock.patch.object(sys.stderr, "isatty", return_value=True):
            with pytest.raises(SystemExit) as exc:
                parse_args(["--help"])
        assert_exit_code(exc, 0)
        out = capsys.readouterr().out
        assert "\033[1mskillsmp\033[0m" in out

    def test_full_help_no_ansi_when_not_tty(self, capsys):
        with mock.patch.object(sys.stderr, "isatty", return_value=False):
            with pytest.raises(SystemExit) as exc:
                parse_args(["--help"])
        assert_exit_code(exc, 0)
        out = capsys.readouterr().out
        assert "\033[1m" not in out

    def test_concise_help_uses_ansi_bold_when_tty(self, capsys):
        with mock.patch.object(sys.stderr, "isatty", return_value=True):
            with pytest.raises(SystemExit):
                parse_args([])
        err = capsys.readouterr().err
        assert "\033[1mskillsmp\033[0m" in err


class TestErrorMessages:
    @pytest.mark.parametrize(
        ("argv", "needle"),
        [
            (["--bogus", "q"], "unknown flag: --bogus"),
            (["--json", "--plain", "q"], "mutually exclusive"),
            (["--limit", "200", "q"], "1-100"),
            (["--sort", "name", "q"], "'stars' or 'recent'"),
            (["--ai", "--limit", "5", "q"], "do not apply"),
        ],
    )
    def test_usage_errors_are_actionable(self, argv, needle, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_args(argv)
        assert_exit_code(exc, 2)
        err = capsys.readouterr().err
        assert needle in err
        assert "Try \"skillsmp --help\" for usage." in err


class TestKeywordOutput:
    def test_human_output(self, capsys, mock_urlopen, make_keyword_response):
        with mock_urlopen(make_keyword_response(total=1)):
            skillsmp._cmd_search("terraform")
        out = capsys.readouterr().out
        assert "Keyword search:" in out
        assert "acme/terraform-deploy" in out
        assert "42 stars" in out

    def test_json_output(self, capsys, mock_urlopen, make_keyword_response):
        with mock_urlopen(make_keyword_response()):
            skillsmp._cmd_search("terraform", output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "keyword"
        assert data["query"] == "terraform"
        assert data["skills"][0]["name"] == "terraform-deploy"

    def test_plain_output(self, capsys, mock_urlopen, make_keyword_response):
        with mock_urlopen(make_keyword_response()):
            skillsmp._cmd_search("terraform", output_plain=True)
        line = capsys.readouterr().out.strip()
        parts = line.split("\t")
        assert parts[0] == "acme/terraform-deploy"
        assert parts[1] == "42"

    def test_plain_output_with_multiple_results_prints_one_line_each(
        self, capsys, mock_urlopen, make_keyword_response, make_skill
    ):
        skills = [make_skill(name="one"), make_skill(name="two")]
        with mock_urlopen(make_keyword_response(skills=skills, total=2)):
            skillsmp._cmd_search("terraform", output_plain=True)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("acme/one\t")
        assert lines[1].startswith("acme/two\t")

    def test_no_results_human(self, capsys, mock_urlopen, make_keyword_response):
        with mock_urlopen(make_keyword_response(skills=[], total=0)):
            skillsmp._cmd_search("none")
        assert "No results found" in capsys.readouterr().out


class TestAIOutput:
    def test_human_output(self, capsys, mock_urlopen, make_ai_response):
        with mock_urlopen(make_ai_response()):
            skillsmp._cmd_ai_search("optimize")
        out = capsys.readouterr().out
        assert "AI search:" in out
        assert "relevance: 0.95" in out

    def test_json_output(self, capsys, mock_urlopen, make_ai_response):
        with mock_urlopen(make_ai_response()):
            skillsmp._cmd_ai_search("optimize", output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "semantic"
        assert data["skills"][0]["relevanceScore"] == 0.95

    def test_plain_output(self, capsys, mock_urlopen, make_ai_response):
        with mock_urlopen(make_ai_response()):
            skillsmp._cmd_ai_search("optimize", output_plain=True)
        parts = capsys.readouterr().out.strip().split("\t")
        assert parts[0] == "acme/terraform-deploy"
        assert parts[-1] == "0.95"

    def test_plain_output_with_multiple_results_prints_one_line_each(
        self, capsys, mock_urlopen, make_ai_response, make_skill
    ):
        entries = [
            {"skill": make_skill(name="one"), "score": 0.9},
            {"skill": make_skill(name="two"), "score": 0.8},
        ]
        with mock_urlopen(make_ai_response(entries=entries)):
            skillsmp._cmd_ai_search("q", output_plain=True)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("acme/one\t")
        assert lines[1].startswith("acme/two\t")

    def test_results_without_metadata_are_reported(
        self, capsys, mock_urlopen, make_ai_response, make_skill
    ):
        entries = [{"skill": make_skill(), "score": 0.9}, {"score": 0.5}]
        with mock_urlopen(make_ai_response(entries=entries)):
            skillsmp._cmd_ai_search("q")
        assert "without full metadata" in capsys.readouterr().out


class TestAPIClient:
    def test_request_url_headers_and_timeout(self, mock_urlopen):
        with mock_urlopen({"data": {}}) as patched:
            skillsmp._api_request("search", {"q": "test", "limit": 10})

        request = patched.call_args.args[0]
        kwargs = patched.call_args.kwargs
        assert request.full_url.startswith(f"{skillsmp.BASE_URL}/search?")
        assert "q=test" in request.full_url
        assert "limit=10" in request.full_url
        assert request.get_header("Authorization") == f"Bearer {FAKE_API_KEY}"
        assert request.get_header("User-agent").startswith("skillsmp-cli/")
        assert kwargs["timeout"] == skillsmp.REQUEST_TIMEOUT

    def test_none_params_are_omitted_from_query(self, mock_urlopen):
        with mock_urlopen({"data": {}}) as patched:
            skillsmp._api_request("search", {"q": "x", "page": None})
        assert "page" not in patched.call_args.args[0].full_url

    def test_http_error_stderr_mode_exits_1(self, capsys):
        err = make_http_error(403, "Forbidden", b'{"error":{"message":"bad key"}}')
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=err):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"})
        assert_exit_code(exc, 1)
        assert "bad key" in capsys.readouterr().err

    def test_http_error_json_mode_exits_1_with_json_output(self, capsys):
        err = make_http_error(429, "Rate limited", b'{"error":{"message":"slow down"}}')
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=err):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"}, use_json_errors=True)
        assert_exit_code(exc, 1)
        data = json.loads(capsys.readouterr().out)
        assert data == {"error": "slow down", "code": 429}

    def test_http_error_falls_back_to_reason_when_body_not_json(self, capsys):
        err = make_http_error(500, "Internal", b"not-json")
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=err):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"})
        assert_exit_code(exc, 1)
        assert "Internal" in capsys.readouterr().err

    def test_network_error_stderr_mode_exits_1(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")
        ):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"})
        assert_exit_code(exc, 1)
        assert "Connection refused" in capsys.readouterr().err

    def test_network_error_json_mode_exits_1_with_json_output(self, capsys):
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"}, use_json_errors=True)
        assert_exit_code(exc, 1)
        data = json.loads(capsys.readouterr().out)
        assert "timeout" in data["error"]


class TestTTYDependentBehavior:
    def test_bold_wraps_when_stderr_is_tty(self):
        with mock.patch.object(sys.stderr, "isatty", return_value=True):
            assert skillsmp._bold("hello") == "\033[1mhello\033[0m"

    def test_bold_is_plain_when_stderr_is_not_tty(self):
        with mock.patch.object(sys.stderr, "isatty", return_value=False):
            assert skillsmp._bold("hello") == "hello"

    def test_no_results_hint_shown_on_tty(self, capsys, mock_urlopen, make_keyword_response):
        with mock_urlopen(make_keyword_response(skills=[], total=0)), mock.patch.object(
            sys.stderr, "isatty", return_value=True
        ):
            skillsmp._cmd_search("none")
        assert "Tip:" in capsys.readouterr().err

    def test_no_results_hint_suppressed_when_not_tty(
        self, capsys, mock_urlopen, make_keyword_response
    ):
        with mock_urlopen(make_keyword_response(skills=[], total=0)), mock.patch.object(
            sys.stderr, "isatty", return_value=False
        ):
            skillsmp._cmd_search("none")
        assert "Tip:" not in capsys.readouterr().err

    def test_ai_progress_indicator_on_tty(self, capsys, mock_urlopen, make_ai_response):
        with mock_urlopen(make_ai_response()), mock.patch("skillsmp._stderr_is_tty", return_value=True), mock.patch(
            "sys.argv", ["skillsmp", "--ai", "query"]
        ):
            skillsmp.main()
        assert "Searching (AI)..." in capsys.readouterr().err

    def test_ai_progress_indicator_not_shown_for_json_mode(
        self, capsys, mock_urlopen, make_ai_response
    ):
        with mock_urlopen(make_ai_response()), mock.patch("skillsmp._stderr_is_tty", return_value=True), mock.patch(
            "sys.argv", ["skillsmp", "--ai", "--json", "query"]
        ):
            skillsmp.main()
        assert "Searching" not in capsys.readouterr().err


class TestFormattingHelpers:
    def test_format_stars(self):
        assert skillsmp._format_stars(None) == "0"
        assert skillsmp._format_stars(42) == "42"
        assert skillsmp._format_stars(1500) == "1.5k"

    def test_format_timestamp(self):
        assert skillsmp._format_timestamp(None) == "unknown"
        assert skillsmp._format_timestamp(0) == "unknown"
        assert skillsmp._format_timestamp(1700000000) == "2023-11-14"

    def test_normalize_skill_defaults_and_score(self, make_skill):
        defaults = skillsmp._normalize_skill({})
        assert defaults["name"] == "unknown"
        assert defaults["author"] == "unknown"
        assert defaults["stars"] == 0
        assert "relevanceScore" not in defaults

        with_score = skillsmp._normalize_skill(make_skill(), score=0.87654)
        assert with_score["relevanceScore"] == 0.8765

    def test_description_truncation(self, capsys, make_skill):
        skillsmp._print_skill(make_skill(description="x" * 300))
        out = capsys.readouterr().out
        assert "x" * skillsmp.DESC_DISPLAY_LIMIT in out
        assert "x" * (skillsmp.DESC_DISPLAY_LIMIT + 1) not in out

        skillsmp._print_skill_plain(make_skill(description="y" * 200))
        plain = capsys.readouterr().out.strip().split("\t")
        assert len(plain[2]) == skillsmp.DESC_PLAIN_LIMIT


class TestEnvironmentAndConfig:
    def test_get_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("SKILLSMP_API_KEY", "from-env")
        assert skillsmp._get_api_key() == "from-env"

    def test_get_api_key_from_dotenv_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
        (tmp_path / ".env").write_text("SKILLSMP_API_KEY=from-file\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert skillsmp._get_api_key() == "from-file"

    def test_dotenv_supports_export_and_quotes(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
        (tmp_path / ".env").write_text("export SKILLSMP_API_KEY='quoted-key'\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert skillsmp._get_api_key() == "quoted-key"

    def test_dotenv_ignores_comments_and_blank_lines(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
        (tmp_path / ".env").write_text(
            textwrap.dedent(
                """\
                # comment

                SKILLSMP_API_KEY=found-it
                """
            )
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        assert skillsmp._get_api_key() == "found-it"

    def test_dotenv_only_loads_api_key_not_unrelated_variables(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
        monkeypatch.delenv("OTHER_VAR", raising=False)
        (tmp_path / ".env").write_text("OTHER_VAR=leak\nSKILLSMP_API_KEY=ok\n")
        monkeypatch.setenv("HOME", str(tmp_path))

        assert skillsmp._get_api_key() == "ok"
        assert "OTHER_VAR" not in skillsmp.os.environ

    def test_missing_api_key_exits_2(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SystemExit) as exc:
            skillsmp._get_api_key()
        assert_exit_code(exc, 2)


class TestMainIntegration:
    def test_keyword_search_path(self, capsys, mock_urlopen, make_keyword_response):
        with mock_urlopen(make_keyword_response()), mock.patch("sys.argv", ["skillsmp", "terraform"]):
            skillsmp.main()
        assert "Keyword search:" in capsys.readouterr().out

    def test_ai_search_path(self, capsys, mock_urlopen, make_ai_response):
        with mock_urlopen(make_ai_response()), mock.patch("sys.argv", ["skillsmp", "--ai", "deploy"]):
            skillsmp.main()
        assert "AI search:" in capsys.readouterr().out

    def test_json_mode_paths(self, capsys, mock_urlopen, make_keyword_response, make_ai_response):
        with mock_urlopen(make_keyword_response()), mock.patch(
            "sys.argv", ["skillsmp", "--json", "terraform"]
        ):
            skillsmp.main()
        keyword = json.loads(capsys.readouterr().out)
        assert keyword["mode"] == "keyword"

        with mock_urlopen(make_ai_response()), mock.patch(
            "sys.argv", ["skillsmp", "--ai", "--json", "deploy"]
        ):
            skillsmp.main()
        semantic = json.loads(capsys.readouterr().out)
        assert semantic["mode"] == "semantic"


CATEGORIES_PAYLOAD = {
    "domains": [
        {
            "domain": "devops",
            "domainName": "DevOps",
            "categories": [
                {"slug": "ci-cd", "name": "CI CD", "count": 1200},
                {"slug": "system-administration", "name": "System Administration", "count": 106011},
            ],
        },
        {
            "domain": "data-ai",
            "domainName": "Data & AI",
            "categories": [{"slug": "llm-ai", "name": "LLM AI", "count": 95606}],
        },
    ]
}


def make_mcp_response(payload, sse=False):
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }
    if sse:
        return make_raw_response(f"event: message\ndata: {json.dumps(msg)}\n\n".encode())
    return make_response(msg)


OCCUPATIONS_SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://skillsmp.com/occupations/software-developers</loc></url>
  <url><loc>https://skillsmp.com/occupations/lawyers</loc></url>
</urlset>
"""


class TestCategoriesCommand:
    def test_human_output_groups_by_domain(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_mcp_response(CATEGORIES_PAYLOAD),
        ) as patched:
            skillsmp._cmd_categories()
        out = capsys.readouterr().out
        assert "3 categories across 2 domains" in out
        assert "DevOps" in out
        assert "ci-cd" in out
        assert "106.0k skills" in out
        assert "--category <slug>" in out
        request = patched.call_args.args[0]
        assert request.full_url == skillsmp.MCP_URL
        body = json.loads(request.data)
        assert body["params"]["name"] == "list_categories"

    def test_parses_sse_response(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_mcp_response(CATEGORIES_PAYLOAD, sse=True),
        ):
            skillsmp._cmd_categories()
        assert "ci-cd" in capsys.readouterr().out

    def test_json_output_passes_through(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_mcp_response(CATEGORIES_PAYLOAD),
        ):
            skillsmp._cmd_categories(output_json=True)
        assert json.loads(capsys.readouterr().out) == CATEGORIES_PAYLOAD

    def test_plain_output_one_line_per_category(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_mcp_response(CATEGORIES_PAYLOAD),
        ):
            skillsmp._cmd_categories(output_plain=True)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 3
        assert lines[0].split("\t") == ["ci-cd", "CI CD", "devops", "1200"]

    def test_mcp_error_exits_1(self, capsys):
        msg = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "boom"}}
        with mock.patch(
            "skillsmp.urllib.request.urlopen", return_value=make_response(msg)
        ):
            with pytest.raises(SystemExit) as exc:
                skillsmp._cmd_categories()
        assert_exit_code(exc, 1)
        assert "boom" in capsys.readouterr().err

    def test_mcp_tool_error_result_exits_1(self, capsys):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"isError": True, "content": [{"type": "text", "text": "nope"}]},
        }
        with mock.patch(
            "skillsmp.urllib.request.urlopen", return_value=make_response(msg)
        ):
            with pytest.raises(SystemExit) as exc:
                skillsmp._cmd_categories()
        assert_exit_code(exc, 1)
        assert "nope" in capsys.readouterr().err


class TestOccupationsCommand:
    def test_human_output_sorted_with_header(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_raw_response(OCCUPATIONS_SITEMAP),
        ) as patched:
            skillsmp._cmd_occupations()
        out = capsys.readouterr().out
        assert "2 occupations" in out
        assert out.index("lawyers") < out.index("software-developers")
        assert patched.call_args.args[0].full_url == skillsmp.OCCUPATIONS_SITEMAP_URL

    def test_plain_output_slugs_only(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_raw_response(OCCUPATIONS_SITEMAP),
        ):
            skillsmp._cmd_occupations(output_plain=True)
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines == ["lawyers", "software-developers"]

    def test_json_output(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_raw_response(OCCUPATIONS_SITEMAP),
        ):
            skillsmp._cmd_occupations(output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data == {"total": 2, "occupations": ["lawyers", "software-developers"]}


class TestGithubRawUrls:
    def test_tree_url_yields_one_candidate(self):
        urls = skillsmp._github_raw_urls(
            "https://github.com/acme/repo/tree/main/plugins/deploy"
        )
        assert urls == [
            "https://raw.githubusercontent.com/acme/repo/main/plugins/deploy/SKILL.md"
        ]

    def test_root_url_tries_main_then_master(self):
        urls = skillsmp._github_raw_urls("https://github.com/acme/repo")
        assert urls == [
            "https://raw.githubusercontent.com/acme/repo/main/SKILL.md",
            "https://raw.githubusercontent.com/acme/repo/master/SKILL.md",
        ]

    def test_non_github_url_yields_nothing(self):
        assert skillsmp._github_raw_urls("https://gitlab.com/acme/repo") == []
        assert skillsmp._github_raw_urls("") == []


class TestShowCommand:
    def test_human_output_with_skill_md(self, capsys, make_skill, make_keyword_response):
        responses = [
            make_response(make_keyword_response(skills=[make_skill()])),
            make_raw_response(b"# Terraform Deploy\n\nInstructions here."),
        ]
        with mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=responses
        ) as patched:
            skillsmp._cmd_show("acme/terraform-deploy")
        out = capsys.readouterr().out
        assert "acme/terraform-deploy" in out
        assert "42 stars" in out
        assert "Deploy infrastructure with Terraform" in out
        assert "--- SKILL.md ---" in out
        assert "Instructions here." in out
        raw_request = patched.call_args_list[1].args[0]
        assert raw_request.full_url == (
            "https://raw.githubusercontent.com/acme/terraform-deploy/main/SKILL.md"
        )

    def test_match_is_case_insensitive(self, capsys, make_skill, make_keyword_response):
        responses = [
            make_response(make_keyword_response(skills=[make_skill()])),
            make_raw_response(b"content"),
        ]
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=responses):
            skillsmp._cmd_show("ACME/Terraform-Deploy")
        assert "acme/terraform-deploy" in capsys.readouterr().out

    def test_json_output_includes_skill_md(self, capsys, make_skill, make_keyword_response):
        responses = [
            make_response(make_keyword_response(skills=[make_skill()])),
            make_raw_response(b"# doc"),
        ]
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=responses):
            skillsmp._cmd_show("acme/terraform-deploy", output_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["name"] == "terraform-deploy"
        assert data["skillMd"] == "# doc"

    def test_not_found_exits_1(self, capsys, make_keyword_response):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_response(make_keyword_response(skills=[], total=0)),
        ):
            with pytest.raises(SystemExit) as exc:
                skillsmp._cmd_show("acme/nonexistent")
        assert_exit_code(exc, 1)
        assert "skill not found: acme/nonexistent" in capsys.readouterr().err

    def test_skill_md_fetch_failure_still_shows_details(
        self, capsys, make_skill, make_keyword_response
    ):
        responses = [
            make_response(make_keyword_response(skills=[make_skill()])),
            make_http_error(404, "Not Found"),
            make_http_error(404, "Not Found"),
        ]
        with mock.patch("skillsmp.urllib.request.urlopen", side_effect=responses):
            skillsmp._cmd_show("acme/terraform-deploy")
        captured = capsys.readouterr()
        assert "acme/terraform-deploy" in captured.out
        assert "could not fetch SKILL.md" in captured.err


class TestSearchFilters:
    def test_category_and_occupation_sent_as_params(self, mock_urlopen):
        with mock_urlopen({"data": {}}) as patched:
            skillsmp._cmd_search("q", category="devops", occupation="lawyers")
        url = patched.call_args.args[0].full_url
        assert "category=devops" in url
        assert "occupation=lawyers" in url

    def test_filters_omitted_when_none(self, mock_urlopen):
        with mock_urlopen({"data": {}}) as patched:
            skillsmp._cmd_search("q")
        url = patched.call_args.args[0].full_url
        assert "category" not in url
        assert "occupation" not in url


class TestSubcommandIntegration:
    def test_categories_path(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_mcp_response(CATEGORIES_PAYLOAD),
        ), mock.patch("sys.argv", ["skillsmp", "categories"]):
            skillsmp.main()
        assert "ci-cd" in capsys.readouterr().out

    def test_occupations_path(self, capsys):
        with mock.patch(
            "skillsmp.urllib.request.urlopen",
            return_value=make_raw_response(OCCUPATIONS_SITEMAP),
        ), mock.patch("sys.argv", ["skillsmp", "occupations"]):
            skillsmp.main()
        assert "lawyers" in capsys.readouterr().out

    def test_show_path(self, capsys, make_skill, make_keyword_response):
        responses = [
            make_response(make_keyword_response(skills=[make_skill()])),
            make_raw_response(b"content"),
        ]
        with mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=responses
        ), mock.patch("sys.argv", ["skillsmp", "show", "acme/terraform-deploy"]):
            skillsmp.main()
        assert "acme/terraform-deploy" in capsys.readouterr().out


def _no_sleep():
    return mock.patch("skillsmp.time.sleep")


class TestRetry:
    def test_retryable_error_succeeds_on_second_attempt(self, capsys):
        error = make_http_error(500, "Internal Server Error")
        success = make_response({"data": {"skills": []}})
        with _no_sleep() as sleep_mock, mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=[error, success]
        ):
            result = skillsmp._api_request("search", {"q": "x"})
        assert result == {"data": {"skills": []}}
        assert sleep_mock.call_count == 1
        assert "retrying" in capsys.readouterr().err

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_retryable_status_codes(self, code):
        error = make_http_error(code)
        success = make_response({"data": {}})
        with _no_sleep(), mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=[error, success]
        ):
            skillsmp._api_request("search", {"q": "x"})

    def test_network_error_retried(self, capsys):
        error = urllib.error.URLError("Connection refused")
        success = make_response({"data": {}})
        with _no_sleep() as sleep_mock, mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=[error, success]
        ):
            result = skillsmp._api_request("search", {"q": "x"})
        assert result == {"data": {}}
        assert sleep_mock.call_count == 1
        assert "retrying" in capsys.readouterr().err

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 429])
    def test_non_retryable_errors_fail_immediately(self, code):
        error = make_http_error(code, body=b'{"error":{"message":"bad"}}')
        with _no_sleep() as sleep_mock, mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=error
        ):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"})
        assert_exit_code(exc, 1)
        assert sleep_mock.call_count == 0

    def test_all_retries_exhausted_exits_1(self, capsys):
        errors = [make_http_error(500, "Internal") for _ in range(3)]
        with _no_sleep() as sleep_mock, mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=errors
        ):
            with pytest.raises(SystemExit) as exc:
                skillsmp._api_request("search", {"q": "x"})
        assert_exit_code(exc, 1)
        assert sleep_mock.call_count == 2
        err = capsys.readouterr().err
        assert "retrying" in err
        assert "API error (500)" in err

    def test_retry_message_shows_retries_left(self, capsys):
        errors = [make_http_error(500) for _ in range(3)]
        with _no_sleep(), mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=errors
        ):
            with pytest.raises(SystemExit):
                skillsmp._api_request("search", {"q": "x"})
        err = capsys.readouterr().err
        assert "2 left" in err
        assert "1 left" in err

    def test_retry_does_not_corrupt_json_output(self, capsys):
        error = make_http_error(500, body=b'{"error":{"message":"down"}}')
        errors = [error] * 3
        with _no_sleep(), mock.patch(
            "skillsmp.urllib.request.urlopen", side_effect=errors
        ):
            with pytest.raises(SystemExit):
                skillsmp._api_request("search", {"q": "x"}, use_json_errors=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["code"] == 500
        assert "retrying" in captured.err
        assert "retrying" not in captured.out

    def test_retry_delay_uses_exponential_backoff(self):
        d0 = skillsmp._retry_delay(0)
        d1 = skillsmp._retry_delay(1)
        # attempt 0: base * 1.0-1.3 => 1.0-1.3
        assert 1.0 <= d0 <= 1.3
        # attempt 1: base * 2 * 1.0-1.3 => 2.0-2.6
        assert 2.0 <= d1 <= 2.6

    def test_is_retryable(self):
        assert skillsmp._is_retryable(make_http_error(500)) is True
        assert skillsmp._is_retryable(make_http_error(502)) is True
        assert skillsmp._is_retryable(make_http_error(403)) is False
        assert skillsmp._is_retryable(make_http_error(429)) is False
        assert skillsmp._is_retryable(urllib.error.URLError("timeout")) is True
