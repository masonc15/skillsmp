# skillsmp

Search the [SkillsMP marketplace](https://skillsmp.com) for agent skills from the command line.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```
uv tool install skillsmp
```

Or run without installing:

```
uvx skillsmp terraform
```

Set your API key in `~/.env` or export it directly:

```
SKILLSMP_API_KEY=your-key-here
```

## Usage

```
skillsmp terraform
skillsmp --limit 5 --sort recent react testing
skillsmp show wshobson/terraform-module-library
```

Use `--json` for structured output or `--plain` for tab-separated lines that pipe to `grep` and `awk`:

```
skillsmp --json deployment
skillsmp --plain react | grep facebook
```

## How search works

Search matches your query against skill names and descriptions, sorted by stars (or by `--sort recent`). It's fast (~300ms) and supports pagination, but ranks by popularity rather than relevance, so a specific query beats a vague one.

SkillsMP used to offer an AI semantic search, and older versions of this tool exposed it as `--ai`. SkillsMP retired it in 2026, so `--ai` now exits with an error.

## Filtering

Search accepts `--category` and `--occupation` to narrow results by SkillsMP's taxonomy, and `--language` to keep only skills written in one language (an ISO code like `en`, `zh` or `ja`; `mul` means mixed and `und` undetermined):

```
skillsmp --category cicd deployment
skillsmp --occupation lawyers --language en contracts
```

An unknown occupation or language is an error, but an unknown category just returns no results. To discover valid slugs, use the `categories` and `occupations` commands. Neither needs an API key — categories come from the SkillsMP MCP server and occupations from the site's public sitemap:

```
skillsmp categories
skillsmp occupations | grep engineer
```

## Skill details

`show` prints everything about one skill — full description, links, and its SKILL.md fetched straight from GitHub:

```
skillsmp show wshobson/terraform-module-library
skillsmp show --json wshobson/terraform-module-library
```

## Flags

```
-n, --limit N       Results per page (1-50, default: 10)
-p, --page N        Page number (default: 1)
-s, --sort KEY      Sort by: stars, recent (default: stars)
    --category C    Filter by category slug
    --occupation O  Filter by occupation slug
    --language L    Filter by content language code
-j, --json          JSON output
    --plain         Tab-separated, one line per result
-h, --help          Show help
    --version       Show version
```

`--limit`, `--page`, `--sort`, and the filters apply to search only.
