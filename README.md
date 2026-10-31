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

It works without an API key, but anonymous use is capped at 50 searches a day and 10 a minute. A free key from [skillsmp.com/developers](https://skillsmp.com/developers) raises that to 500 a day and 30 a minute. Set it in `~/.env` or export it directly:

```
SKILLSMP_API_KEY=your-key-here
```

## Usage

```
skillsmp terraform
skillsmp --limit 5 --sort recent react testing
skillsmp show wshobson/terraform-module-library
```

Results often include translations of the same skill, so anything not in English is tagged with its language code, like `affaan-m/python-patterns (es)`. Use `--json` for structured output or `--plain` for tab-separated lines (name, stars, description, GitHub URL, language) that pipe to `grep` and `awk`:

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

`-x`/`--exclude` drops results whose `author/name` or description contains a term (case-insensitive). Repeat it to exclude several things. The API has no exclusion of its own, so the CLI fetches extra pages to fill `--limit` and counts `--page` over what's left, giving up after 5 requests:

```
skillsmp -x native -x affaan-m react
```

## Interactive mode

`-i` opens a full-screen browser instead of printing results. The list is on the left and the highlighted skill's details and SKILL.md are on the right. It takes the same flags as a normal search, and the query is optional:

```
skillsmp -i react
skillsmp -i --sort recent --language en
```

Move with the arrow keys or `j`/`k`, and change pages with `←`/`→` (or `[`/`]`). The next page is fetched in the background, so paging forward is usually instant. `/` jumps to the search box, which re-runs as you type. A word starting with `-` in the box works like `--exclude`, so `react -native` hides anything mentioning native. `x` hides every skill with the highlighted skill's name and `X` hides its author; both add a term to the search box, so deleting it there brings them back. `s` switches between stars and recent, Enter opens SKILL.md full screen, `o` opens the skill on GitHub, `w` opens its SkillsMP page, `y` copies its GitHub URL, and `q` quits.

## Skill details

`show` prints everything about one skill — full description, links, and its SKILL.md fetched straight from GitHub:

```
skillsmp show wshobson/terraform-module-library
skillsmp show --json wshobson/terraform-module-library
```

## Flags

```
-i, --interactive   Browse results in a full-screen TUI
-n, --limit N       Results per page (1-50, default: 10, or 20 with -i)
-p, --page N        Page number (default: 1)
-s, --sort KEY      Sort by: stars, recent (default: stars)
    --category C    Filter by category slug
    --occupation O  Filter by occupation slug
    --language L    Filter by content language code
-x, --exclude T     Drop results mentioning T (repeatable)
-j, --json          JSON output
    --plain         Tab-separated, one line per result
-h, --help          Show help
    --version       Show version
```

`--limit`, `--page`, `--sort`, `--exclude`, and the filters apply to search only.
