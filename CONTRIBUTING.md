# Contributing to dipesh-skills

Thanks for helping. This repo holds general-purpose [Agent Skills](https://docs.claude.com/en/docs/claude-code/skills) and the scripts that install them. Contributions of all sizes are welcome: bug reports, doc fixes, improvements to existing skills, new skills and new recommended third-party skills.

## Ground rules

- `main` is protected. Nobody pushes to it directly: every change lands through a pull request approved by the maintainer (see [`.github/CODEOWNERS`](.github/CODEOWNERS)).
- Be kind and assume good intent. Harassment or abuse gets you removed from the project.
- Never commit secrets, tokens, API keys, personal paths, browser profiles or `.env` files.
- Security problems: don't open a public issue. Use GitHub's **Report a vulnerability** (Security tab) instead.

## Workflow

1. Open an issue first for anything bigger than a small fix, so we agree on the approach before you write it.
2. Fork the repo and create a branch from `main`: `fix/<short-name>`, `feat/<short-name>` or `docs/<short-name>`.
3. Make the change, keeping one skill or one concern per PR.
4. Test it (see below).
5. Open a pull request against `main` and fill in the template. Keep the PR small and focused; resolve review comments before merge.

Commit messages: short imperative subject (`Add X`, `Fix Y`), body explaining *why* when it isn't obvious. [Conventional Commits](https://www.conventionalcommits.org/) prefixes (`feat:`, `fix:`, `docs:`) are welcome but not required.

## Repo layout

```
.claude-plugin/marketplace.json      Claude Code plugin marketplace (lists every plugin)
plugins/<plugin>/.claude-plugin/     plugin manifest
plugins/<plugin>/skills/<skill>/     the skill: SKILL.md plus optional scripts/, references/
recommended-skills.txt               third-party skills the installers fetch
install.sh / install.ps1             installers (Linux/macOS/WSL and Windows)
```

## Writing or changing a skill

- `SKILL.md` needs YAML frontmatter with `name` (matches the folder) and `description`. The description decides when an agent loads the skill: say what it does **and** when to use it, with the phrases users actually type.
- Keep skills general: no project-, company- or machine-specific assumptions. Let the skill read the repo's own rules (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`) instead of hard-coding them.
- Keep `SKILL.md` focused; move long reference material into `references/` and load it only when needed.
- Scripts must work on Linux, macOS and Windows where the skill claims to, use only what the setup installs, and never touch user data without asking (the browser skill copies profiles, it never opens the real ones).
- Never make a skill commit, push, pay, send messages or delete data without the user's explicit go-ahead.

### Adding a new skill

1. Create `plugins/<name>/.claude-plugin/plugin.json` and `plugins/<name>/skills/<name>/SKILL.md`.
2. Add the plugin to `.claude-plugin/marketplace.json`.
3. Add a row to the skills table and the "Where each skill runs" table in `README.md`.

The installers pick up any `plugins/*/skills/*` folder automatically.

### Recommending a third-party skill

Add one line to `recommended-skills.txt`: `<skill-name> <github owner/repo> <path inside repo>` (`.` for the repo root), and a row to the README table. Only recommend actively maintained, permissively licensed skills you have tried.

## Testing

Run what applies before opening the PR:

```bash
bash -n install.sh                                  # syntax
claude plugin validate .                            # marketplace + plugin manifests
./install.sh --dir /tmp/skills-test --yes           # real install into a throwaway dir
./install.sh --dir /tmp/skills-test --list
python3 -m py_compile plugins/*/skills/*/scripts/*.py
```

Then try the skill for real in at least one agent (Claude Code, Codex or another) and say in the PR which one and on what OS. If you change `install.sh`, make the same change in `install.ps1` (and say if you couldn't test it on Windows).

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
