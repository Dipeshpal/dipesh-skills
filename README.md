# dipesh-skills

General-purpose [Agent Skills](https://docs.claude.com/en/docs/claude-code/skills) for software delivery. Not tied to any project, language or framework.

| Skill | What it does |
|---|---|
| [`dipesh-task-planner-skill`](plugins/dipesh-task-planner-skill/skills/dipesh-task-planner-skill/SKILL.md) | Plan-first workflow for any non-trivial coding task. Takes a requirement from idea to verified, documented code: understand → recall the repo → ask doubts → **one plan, one approval** → docs alongside code → execute in parallel waves (subagents where available) → full verification → hand over with a manual test checklist. Never commits/pushes until asked; never destroys data. |
| [`dipesh-browser-control`](plugins/dipesh-browser-control/skills/dipesh-browser-control/SKILL.md) | Lets the agent drive a real browser through one CLI (`bc`): finds every installed browser and profile (Chrome, Edge, Brave, Vivaldi, Opera, Chromium, Firefox), uses a **copy** of the profile you pick or a standalone browser, runs on Playwright and sees into shadow DOM and iframes. It fills whole forms in one verified call, and can use a decision model (local **OpenJev-1.7B/4B**, local **Kev-0.6B browser-use**, or the hosted **Jev API**) to `ask` yes/choose/rate questions about the page or `act` (pick, execute, verify), with confidence thresholds and no silent guessing (ideas from [PlayJev](https://github.com/filedcom/playjev)). It learns site recipes and past mistakes into project-level or user-level memory linked from `CLAUDE.md` / `AGENTS.md`. |

### Recommended third-party skills

Installed by `install.sh` alongside the pack (skip with `--no-recommended`). Listed in [`recommended-skills.txt`](recommended-skills.txt) — add a line there to recommend more.

| Skill | Source |
|---|---|
| `karpathy-guidelines` | [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) |
| `unslop` | [theclaymethod/unslop](https://github.com/theclaymethod/unslop) |
| `ecc` (Claude Code plugin: agents, skills, hooks, rules) | [affaan-m/ECC](https://github.com/affaan-m/ECC) — the scripts install it with `claude plugin install ecc@ecc` when Claude Code is a target (asked first in interactive mode; skipped with `--no-recommended` or `--dir`). Other agents: use ECC's own installer. |

## Install

### Claude Code plugin (recommended — gets updates)

```text
/plugin marketplace add Dipeshpal/dipesh-skills
/plugin install dipesh-task-planner-skill@dipesh-skills
/plugin install andrej-karpathy-skills@dipesh-skills   # recommended
/plugin install ecc@dipesh-skills                       # recommended (ECC harness)
```

`unslop` ships no Claude plugin manifest, so get it with the script below.

### Script (Claude Code, Codex, any agent that reads `SKILL.md` folders)

```bash
curl -fsSL https://raw.githubusercontent.com/Dipeshpal/dipesh-skills/main/install.sh | bash          # macOS / Linux / WSL / Git Bash
```
```powershell
irm https://raw.githubusercontent.com/Dipeshpal/dipesh-skills/main/install.ps1 | iex                 # Windows PowerShell
```

Arrow-key menus walk you through it:

1. **What to do**: install, update installed skills, uninstall, show installed.
2. **Which agents** (tick one or more): Claude Code (`.claude/skills`), Codex (`.codex/skills`), any agent using the open Agent Skills layout (`.agents/skills`), or a custom dir.
3. **Where**: user scope (`~/.<agent>/skills`), project scope (`./.<agent>/skills`, commit it for the team) or local scope (same, kept out of git).
4. **Which skills**: a checkbox list of this pack plus the recommended skills (Space toggles, `a` all/none).

Every install is recorded in `.dipesh-skills.lock` inside the skills dir (skill, source repo, commit). **Update** refreshes exactly what you installed, in every scope, and prints `old -> new` per skill. Skills with their own dependencies (browser control) also get those upgraded, using the choices made at setup.

Non-interactive flags (append after `bash -s --` when piping from curl):

```bash
./install.sh --yes                         # everything, Claude Code user scope, no questions
./install.sh --agent claude,codex          # one or more of claude (default) | codex | agents, or all
./install.sh --project | --local           # project scope / local scope (any agent)
./install.sh --dir ~/somewhere/skills      # any skills directory
./install.sh dipesh-task-planner-skill     # only the named skill(s)
./install.sh --select                      # checkbox menu for skills only
./install.sh --no-recommended              # only this pack's own skills
./install.sh --only-recommended            # only the third-party ones
./install.sh --update [skill..]            # update everything (or the named skills) installed by the script
./install.sh --uninstall unslop            # remove one (--uninstall --yes removes all)
./install.sh --list                        # installed skills, agent dir, version
./install.sh --package [skill..]           # dist/<skill>.zip to upload to Claude.ai / the Claude API
```

PowerShell takes the same choices as parameters: `-Action install|update|uninstall|list -Agent claude|codex|agents -Scope user|project -Dir <path> -Skills a,b -NoRecommended -OnlyRecommended`.

### Where each skill runs

| | Claude Code | Codex / other local agents | Claude.ai, Claude desktop, Claude API |
|---|---|---|---|
| `dipesh-task-planner-skill` | ✅ (uses subagents) | ✅ (sequential fallback) | ✅ upload the zip from `--package` |
| `dipesh-browser-control` | ✅ | ✅ needs a shell on the machine | ❌ the sandbox has no local browser, profiles or display |
| `karpathy-guidelines` | ✅ | ✅ | ✅ |
| `unslop` | ✅ | ✅ | likely ✅: it bundles Python scripts, which the sandbox can run (untested) |

Plugin installs update with `/plugin marketplace update dipesh-skills`.

### Manual

Copy `plugins/dipesh-task-planner-skill/skills/dipesh-task-planner-skill/` into `~/.claude/skills/` (or `<repo>/.claude/skills/`).

Restart the session after installing.

## Use

```text
/dipesh-task-planner-skill add CSV export to the reports page
```

or just describe the work — it triggers on new features, change requests, refactors, pasted tickets, "plan this task" / "break this down". Say "just plan it" to stop after the plan. Small tasks get a compressed version of the workflow; trivial ones skip it.

### Browser control setup

Installing `dipesh-browser-control` through `install.sh` offers to run its one-time setup right away. Later, or after a plugin install, run:

```bash
~/.claude/skills/dipesh-browser-control/scripts/bc setup      # or <project>/.claude/skills/...
```

The setup asks which browser profile to use (or a standalone browser), which step model to use (none, Jev API key, OpenJev-1.7B ≈3.5 GB, OpenJev-4B ≈8.4 GB, Kev-0.6B browser-use ≈1.5 GB) and where lessons go. It then installs Playwright (plus PyTorch/OpenJev if chosen) into `~/.dipesh-browser-control/venv` and downloads the model. It needs `python3`; [`uv`](https://docs.astral.sh/uv/) is used when present, and OpenJev needs Python 3.11+, which uv provides.

## Make it yours

The skill defers to the repo: it reads `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, rule files and CI config, and follows them over its own defaults. To specialise it for a team, put commands, branch names and hard rules in those files rather than editing the skill.

## License

MIT
