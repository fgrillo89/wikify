# Claude Code Plugin Packaging — Research + Target Layout for Wikify

Sources (official Anthropic docs, fetched 2026-06-28):
- Plugins reference: https://code.claude.com/docs/en/plugins-reference
- Plugin marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Skills: https://code.claude.com/docs/en/skills

This is the authoritative source. Third-party JSON-schema mirrors
(`hesreallyhim/claude-code-json-schema`, schemastore) exist for editor
autocomplete but are unofficial; the docs above are normative.

---

## 1. What a plugin IS

> A plugin is a self-contained directory of components that extends Claude
> Code with custom functionality. Components: skills, agents, hooks, MCP
> servers, LSP servers, monitors.

A plugin is distributed through a **marketplace** (a catalog file users
`/plugin marketplace add`, then `/plugin install`), or loaded directly for a
session via `claude --plugin-dir <path>` / `--plugin-url`. Marketplace
installs are **copied into a versioned cache** (`~/.claude/plugins/cache`);
a plugin cannot reference files outside its own directory after install
(no `../shared-utils`).

---

## 2. `.claude-plugin/plugin.json` schema

The manifest lives at `<plugin-root>/.claude-plugin/plugin.json`. It is
**optional**; if omitted, components are auto-discovered in default
locations and the plugin name is the directory name. If present, **`name`
is the only required field** (kebab-case, no spaces). All component-path
fields must be relative and start with `./`.

```json
{
  "name": "plugin-name",
  "displayName": "Plugin Name",
  "version": "1.2.0",
  "description": "Brief plugin description",
  "author": { "name": "Author Name", "email": "a@example.com", "url": "https://..." },
  "homepage": "https://docs.example.com/plugin",
  "repository": "https://github.com/author/plugin",
  "license": "MIT",
  "keywords": ["keyword1", "keyword2"],
  "skills": "./custom/skills/",
  "commands": ["./custom/commands/special.md"],
  "agents": ["./custom/agents/reviewer.md"],
  "hooks": "./config/hooks.json",
  "mcpServers": "./mcp-config.json",
  "outputStyles": "./styles/",
  "lspServers": "./.lsp.json",
  "experimental": { "themes": "./themes/", "monitors": "./monitors.json" },
  "dependencies": ["helper-lib", { "name": "secrets-vault", "version": "~2.1.0" }]
}
```

Field notes that matter for Wikify:
- `name` — used for namespacing. Skill `foo` in plugin `bar` is invoked as
  `bar:foo`.
- `version` — **optional but load-bearing**. If set, users only get updates
  when you bump it. If omitted (in a git-hosted marketplace) the commit SHA
  is the version, so every commit is an update. For an actively-iterated
  repo, leave it unset; for tagged releases, set it and bump every release.
- `skills` — string|array. **Adds to** the default `skills/` scan (does not
  replace it). Exception: for a marketplace entry whose `source` resolves to
  the marketplace root, listing subdirs replaces the default scan.
- `commands`, `agents`, `outputStyles`, `experimental.*` — listing a custom
  path **replaces** the default folder. To keep the default and add more,
  list it explicitly.
- `mcpServers`, `hooks`, `lspServers` — string|array|object; may be a path or
  inline config; have their own merge rules.
- Unrecognized top-level fields are ignored at load (warned by
  `claude plugin validate`, error under `--strict`). Wrong-typed known
  fields are hard load errors.

Validate with: `claude plugin validate ./<plugin> --strict`.

---

## 3. Where components live (default locations)

All component dirs sit at the **plugin root**, NOT inside `.claude-plugin/`
(only `plugin.json` goes there). A `CLAUDE.md` at the plugin root is NOT
loaded as context — ship instructions as skills.

| Component     | Default location          | Notes |
| :------------ | :------------------------ | :---- |
| Manifest      | `.claude-plugin/plugin.json` | optional |
| Skills        | `skills/<name>/SKILL.md`  | dir-per-skill, may bundle `references/`, `scripts/` |
| Commands      | `commands/*.md`           | flat `.md` skills; prefer `skills/` for new plugins |
| Agents        | `agents/*.md`             | subagent markdown w/ frontmatter |
| Hooks         | `hooks/hooks.json`        | or inline in plugin.json |
| MCP servers   | `.mcp.json`               | or inline `mcpServers` in plugin.json |
| LSP servers   | `.lsp.json`               | |
| Monitors      | `monitors/monitors.json`  | experimental |
| Output styles | `output-styles/`          | |
| Executables   | `bin/`                    | added to Bash `PATH` while enabled |
| Settings      | `settings.json`           | only `agent`/`subagentStatusLine` keys |

Path/env variables substituted in skill content, agent content, hook /
monitor commands, and MCP/LSP configs:
- `${CLAUDE_PLUGIN_ROOT}` — absolute path to the installed plugin dir
  (changes on update; do not persist state here).
- `${CLAUDE_PLUGIN_DATA}` — persistent per-plugin dir surviving updates
  (`~/.claude/plugins/data/<id>/`); use for venvs, caches.
- `${CLAUDE_PROJECT_DIR}` — the project root Claude Code launched from.
- `${user_config.KEY}` — values declared in `userConfig`, prompted at enable.

---

## 4. `.claude-plugin/marketplace.json` schema

Lives at the **repo root** in `.claude-plugin/marketplace.json` (the
"marketplace root" = the dir containing `.claude-plugin/`). Required:
`name`, `owner`, `plugins`.

```json
{
  "name": "wikify-tools",
  "owner": { "name": "Fabio Grillo", "email": "..." },
  "plugins": [
    {
      "name": "wikify",
      "source": "./.claude/skills/wikify",
      "description": "Researcher-style Wikipedia-from-corpus builder for Claude Code",
      "version": "0.1.0"
    }
  ]
}
```

Per-plugin entry: required `name` + `source`. `source` types:

| Source        | Form | Fields |
| :------------ | :--- | :----- |
| Relative path | `"./plugins/x"` (string) | resolves to `<repo>/plugins/x`; must start `./`; no `../` |
| `github`      | object | `repo`, `ref?`, `sha?` |
| `url`         | object | `url`, `ref?`, `sha?` |
| `git-subdir`  | object | `url`, `path`, `ref?`, `sha?` — sparse clone of a monorepo subdir |
| `npm`         | object | `package`, `version?`, `registry?` |

A plugin entry may also carry any plugin-manifest field plus
marketplace-only fields: `source`, `category`, `tags`, `strict`,
`relevance`, `displayName`, `defaultEnabled`. `metadata.pluginRoot` sets a
base dir prepended to relative `source` paths.

Caveat: **relative-path sources only resolve when the marketplace is added
from a git source or local dir**, not when added via a direct URL to the
raw `marketplace.json` (only that one file is downloaded). For a single-repo
release we add the marketplace from the repo (git/local), so relative paths
are fine.

Distribution flow:
```
/plugin marketplace add <owner>/<repo>      # or local ./
/plugin install wikify@wikify-tools
```

---

## 5. The hard part for Wikify: the MCP server + Python package

Wikify is not pure markdown. Two coupled assets:

1. **15 skills** under `.claude/skills/` (3 of them bundle `references/`:
   `wikify`, `wikify-bundle`, `wikify-write-page`, plus `wikify-query`,
   `wikify-search-corpus`, `wikify-search-wiki`). These are plugin-native —
   they package directly.
2. **The `wikify` Python package** (`src/wikify`, console script
   `wikify = "wikify.cli:main"`, exposes `wikify mcp serve`). A Claude Code
   plugin bundles config + markdown, **not a pip-installed Python
   distribution**. The current `.mcp.json` hardcodes a machine path and a
   corpus:

   ```json
   { "command": "uv", "args": ["run","--project",".","wikify","mcp","serve"],
     "env": { "WIKIFY_CORPUS": "${WIKIFY_CORPUS}" } }
   ```

   Both the absolute project path and the hardcoded corpus must go before
   this is shippable.

Recommended resolution:
- **Ship the Python package via PyPI** (`pip install wikify` /
  `uv tool install wikify` / `pipx install wikify`) as a documented
  prerequisite. The plugin's `.mcp.json` then calls the resolved console
  script, not a machine path:

  ```json
  {
    "mcpServers": {
      "wikify": {
        "command": "wikify",
        "args": ["mcp", "serve"],
        "env": { "WIKIFY_CORPUS": "${user_config.corpus}" }
      }
    }
  }
  ```

- **Expose the corpus path via `userConfig`** (prompted at enable time)
  instead of a baked path:

  ```json
  "userConfig": {
    "corpus": {
      "type": "directory",
      "title": "Corpus directory",
      "description": "Path to the built Wikify corpus (data/corpora/<name>)",
      "required": true
    }
  }
  ```

- Alternative if you do NOT want a PyPI dependency: bundle a `SessionStart`
  hook that `uv tool install`s wikify into `${CLAUDE_PLUGIN_DATA}` on first
  run (the docs' node_modules pattern, adapted). Heavier; PyPI is cleaner.

Either way the goal: a freshly-installed plugin on another machine starts
the MCP server with zero hand-edited paths.

---

## 6. Recommended target layout for Wikify

Goal: one installable plugin, **single source of truth for the skills**, and
**zero disruption to the in-repo dev loop**. The
**skills-directory plugin** mechanism achieves all three.

> The docs: "Any folder under a skills directory that contains a
> `.claude-plugin/plugin.json` manifest is loaded as a plugin named
> `<name>@skills-dir` on the next session, with no marketplace and no install
> step." A project-scope one loads from `<cwd>/.claude/skills/` after the
> workspace trust dialog.

So we nest the plugin inside the existing skills dir. In dev it auto-loads
as `wikify@skills-dir`; the SAME directory is what the marketplace ships.

```
scholarforge/
├── .claude-plugin/
│   └── marketplace.json          # catalog; source: "./.claude/skills/wikify"
├── .claude/
│   └── skills/
│       └── wikify/               # <-- PLUGIN ROOT (loads as wikify@skills-dir in dev)
│           ├── .claude-plugin/
│           │   └── plugin.json   # name, version, license, mcpServers, userConfig
│           ├── .mcp.json         # wikify MCP server, ${user_config.corpus}, no abs paths
│           └── skills/           # the 15 skills move here
│               ├── wikify/                       # shared reference skill (+ references/)
│               │   ├── SKILL.md
│               │   └── references/
│               ├── wikify-investigate/SKILL.md
│               ├── wikify-investigate-explore/SKILL.md
│               ├── wikify-arxiv/SKILL.md
│               ├── wikify-baseline/SKILL.md
│               ├── wikify-bundle/{SKILL.md,references/}
│               ├── wikify-write-page/{SKILL.md,references/}
│               ├── wikify-search-corpus/{SKILL.md,references/}
│               ├── wikify-search-wiki/{SKILL.md,references/}
│               ├── wikify-query/{SKILL.md,references/}
│               ├── wikify-organize-wiki/SKILL.md
│               ├── wikify-refine/SKILL.md
│               ├── wikify-extract-data/SKILL.md
│               ├── wikify-consolidate-data/SKILL.md
│               └── wikify-gather-evidence-cluster/SKILL.md
├── src/wikify/                   # Python package, published to PyPI separately
└── ...
```

`.claude/skills/wikify/.claude-plugin/plugin.json`:

```json
{
  "name": "wikify",
  "displayName": "Wikify",
  "description": "Build a navigable Wikipedia-style wiki from a research corpus, with corpus/wiki MCP search tools.",
  "license": "MIT",
  "repository": "https://github.com/<owner>/<repo>",
  "keywords": ["research", "wiki", "corpus", "rag", "arxiv"],
  "mcpServers": "./.mcp.json",
  "userConfig": {
    "corpus": {
      "type": "directory",
      "title": "Corpus directory",
      "description": "Path to a built Wikify corpus (data/corpora/<name>).",
      "required": true
    }
  }
}
```

Notes on this layout:
- The 15 skills currently directly under `.claude/skills/*` move **one level
  down** into `.claude/skills/wikify/skills/*`. After the move, dev mode
  loads them as the `wikify@skills-dir` plugin (namespaced `wikify:<skill>`)
  instead of as 15 loose project skills. This is the only behavioral change
  to the dev loop, and it is what we want for parity with the shipped artifact.
- Single source of truth: skills exist in exactly one place. No symlinks, no
  duplicated `skills/` tree (honors the no-dead-versioning rule).
- `version` is intentionally omitted in `plugin.json` so each commit is an
  update during pre-release; set it (and bump) once you cut tagged releases.
- The shared `wikify` reference skill keeps the name `wikify`; invoked as
  `wikify:wikify`. Acceptable; rename only if the doubled token bothers you.

### Alternative layout (cleaner tree, more dev friction)

Put the plugin at a top-level `plugins/wikify/` instead of under
`.claude/skills/`. Distribution is marginally tidier, but the skills no
longer live in `.claude/skills/`, so dev sessions must load them explicitly
(`claude --plugin-dir plugins/wikify`) or install at project scope each
clone. Recommendation: prefer the skills-dir layout above for minimal
disruption; only switch to `plugins/` if you later split multiple plugins
out of this repo.

---

## 7. Migration checklist (minimal disruption)

1. `mkdir .claude/skills/wikify/.claude-plugin` and move the 15 existing
   skill dirs into `.claude/skills/wikify/skills/` (git-mv, one commit).
2. Add `.claude/skills/wikify/.claude-plugin/plugin.json` (section 6).
3. Move/rewrite `.mcp.json` to `.claude/skills/wikify/.mcp.json`:
   replace the hardcoded `--project C:/dev/scholarforge` invocation with
   `"command": "wikify"`, and `WIKIFY_CORPUS` with `${user_config.corpus}`.
4. Add repo-root `.claude-plugin/marketplace.json` (section 4),
   `source: "./.claude/skills/wikify"`.
5. Publish `wikify` to PyPI (or add the `SessionStart` install hook) so the
   MCP `command: "wikify"` resolves on a clean machine.
6. Validate: `claude plugin validate ./.claude/skills/wikify --strict`.
7. Smoke test from a clean checkout: `/plugin marketplace add ./` then
   `/plugin install wikify@wikify-tools`, confirm skills appear as
   `wikify:*` and the `mcp__wikify__*` tools connect.
8. Update `.gitignore` / docs; ensure `.claude/settings.local.json`
   `enabledPlugins` reflects `wikify@skills-dir` for the dev repo.

### Blast radius
- Touches: `.claude/skills/*` (relocated), `.mcp.json` (moved + de-hardcoded),
  new `.claude-plugin/marketplace.json`, new plugin manifest, packaging docs.
- Any workflow/script referencing `.claude/skills/<skill>` paths directly
  must be updated to `.claude/skills/wikify/skills/<skill>`. Grep
  `.claude/workflows/` and `scripts/` before committing.
- The skill invocation names gain the `wikify:` namespace prefix; any docs
  or prompts that hardcode bare skill names (e.g. `wikify-investigate`)
  should be checked — namespacing is automatic at the tool layer but
  user-facing references should match.
