# skillsmp

Search for agent skills from the command line. It searches [skills.sh](https://skills.sh), the directory behind `npx skills`, and falls back to the [SkillsMP marketplace](https://skillsmp.com) when skills.sh has nothing.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```
uv tool install skillsmp
```

Or run without installing:

```
uvx skillsmp terraform
```

skills.sh needs no key and isn't metered. SkillsMP works without an API key too, but anonymous use is capped at 50 searches a day and 10 a minute. A free key from [skillsmp.com/developers](https://skillsmp.com/developers) raises that to 500 a day and 30 a minute. Set it in `~/.env` or export it directly:

```
SKILLSMP_API_KEY=your-key-here
```

The key is only ever sent to SkillsMP.

## Usage

```
skillsmp terraform
skillsmp --sort installs react testing
skillsmp show hashicorp/agent-skills/terraform-test
```

Each skills.sh result comes with its install count and the `npx skills add` command that installs it. Use `--json` for structured output or `--plain` for tab-separated lines (ID, installs or stars, description, GitHub URL, language, source) that pipe to `grep` and `awk`:

```
skillsmp --json deployment
skillsmp --plain react | grep facebook
```

## Sources

skills.sh is the default. Its catalog comes from what people actually install with `npx skills`, so it's current and ranked by relevance (or by install count with `--sort installs`). Multi-word queries get a semantic search. It returns at most 200 results for a query, all in one request, and the CLI pages through them locally. It has no descriptions in search results, so `show` and the TUI read them from each skill's SKILL.md.

SkillsMP is a bigger crawl of GitHub with stars, categories and per-language filtering. The CLI uses it when skills.sh returns no results or fails, when the query is a single character (skills.sh needs two), or when you ask for something only SkillsMP has: `--category`, `--occupation`, `--language`, or `--sort stars`/`recent`. Results that came from SkillsMP say so in the header (and in `source` in JSON, with a `fallbackReason`).

`--source skills.sh` or `--source skillsmp` picks one and turns off the fallback.

SkillsMP used to offer an AI semantic search, and older versions of this tool exposed it as `--ai`. SkillsMP retired it in 2026, so `--ai` now exits with an error.

## Filtering

SkillsMP search accepts `--category` and `--occupation` to narrow results by SkillsMP's taxonomy, and `--language` to keep only skills written in one language (an ISO code like `en`, `zh` or `ja`; `mul` means mixed and `und` undetermined):

```
skillsmp --category cicd deployment
skillsmp --occupation lawyers --language en contracts
```

An unknown occupation or language is an error, but an unknown category just returns no results. To discover valid slugs, use the `categories` and `occupations` commands. Neither needs an API key — categories come from the SkillsMP MCP server and occupations from the site's public sitemap:

```
skillsmp categories
skillsmp occupations | grep engineer
```

`-x`/`--exclude` works on both sources. It drops results whose ID (`owner/repo/skill` on skills.sh, `author/name` on SkillsMP) or description contains a term (case-insensitive). Repeat it to exclude several things. Neither API has exclusion of its own, so on SkillsMP the CLI fetches extra pages to fill `--limit` and counts `--page` over what's left, giving up after 5 requests:

```
skillsmp -x native -x affaan-m react
```

## Interactive mode

`-i` opens a full-screen browser instead of printing results. The list is on the left and the highlighted skill's details and SKILL.md are on the right. It takes the same flags as a normal search, and the query is optional:

```
skillsmp -i react
skillsmp -i --source skillsmp --sort recent
```

Move with the arrow keys or `j`/`k`, and change pages with `←`/`→` (or `[`/`]`). `/` jumps to the search box and Enter runs the search. A word starting with `-` in the box works like `--exclude`, so `react -native` hides anything mentioning native, and exclusions apply as you type. `x` hides every skill with the highlighted skill's name and `X` hides its author; both add a term to the search box, so deleting it there brings them back. `s` cycles the sort (relevance and installs on skills.sh, stars and recent on SkillsMP) and `b` switches source, which then sticks for later searches. Enter opens SKILL.md full screen, `i` copies the install command, `o` opens the skill on GitHub, `w` opens its skills.sh or SkillsMP page, `y` copies its GitHub URL, and `q` quits.

SkillsMP searches are metered. Anonymous use and the Free plan both get 50 a day, reset at midnight UTC, so the TUI is careful with them. It fetches 50 results per request and builds pages from those, so one search covers the first two and a half pages, and adding or removing exclusions doesn't make a new request. It only fetches the next page ahead of time while more than 10 searches are left, and the status line shows how many remain. None of that applies to skills.sh, where one request covers every page.

## Skill details

`show` prints everything about one skill: full description, links, install command, and its SKILL.md. Give it a skills.sh ID (`owner/repo/skill`, copied from a search result) or just `owner/skill`, which picks the most installed match and lists any others. Skills hosted on a website rather than GitHub, like `docs.stripe.com/stripe-docs`, work too.

```
skillsmp show hashicorp/agent-skills/terraform-test
skillsmp show hashicorp/terraform-test
skillsmp show --source skillsmp --json wshobson/terraform-module-library
```

An `owner/skill` that skills.sh doesn't know falls back to SkillsMP, which fetches SKILL.md straight from GitHub.

## Flags

```
-i, --interactive   Browse results in a full-screen TUI
-n, --limit N       Results per page (1-50, default: 10, or 20 with -i)
-p, --page N        Page number (default: 1)
-s, --sort KEY      skills.sh: relevance (default), installs
                    SkillsMP: stars (default), recent
    --source S      skills.sh or skillsmp (default: skills.sh with fallback)
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
