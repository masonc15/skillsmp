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
skillsmp --ai "how to optimize database queries"
skillsmp show wshobson/terraform-module-library
```

Use `--json` for structured output or `--plain` for tab-separated lines that pipe to `grep` and `awk`:

```
skillsmp --json deployment
skillsmp --plain react | grep facebook
```

## Search modes

The default keyword search matches your query against skill names and descriptions, sorted by stars. It's fast (~300ms) and supports pagination, but ranks by popularity rather than relevance.

`--ai` runs a vector similarity search powered by [Cloudflare Vectorize](https://developers.cloudflare.com/vectorize/). Each skill's full SKILL.md has been embedded, and your query is compared against those embeddings. This returns ~10 results ranked by relevance score, catching semantically related skills that keyword search misses. Pagination and sorting don't apply. Slower (~4-5s) because the query must be embedded first.

## Filtering

Keyword search accepts `--category` and `--occupation` to narrow results by SkillsMP's taxonomy:

```
skillsmp --category devops deployment
skillsmp --occupation lawyers contracts
```

To discover valid slugs, use the `categories` and `occupations` commands. Neither needs an API key — categories come from the SkillsMP MCP server and occupations from the site's public sitemap:

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
-a, --ai            AI semantic search
-n, --limit N       Results per page (1-100, default: 10)
-p, --page N        Page number (default: 1)
-s, --sort KEY      Sort by: stars, recent (default: stars)
    --category C    Filter by category slug
    --occupation O  Filter by occupation slug
-j, --json          JSON output
    --plain         Tab-separated, one line per result
-h, --help          Show help
    --version       Show version
```

`--limit`, `--page`, `--sort`, `--category`, and `--occupation` apply to keyword search only.
