# leha-loop

A Claude Code skill that makes the agent a "feature boss" for any git project. It runs a team of agents in [Herdr](https://herdr.app) (`hc`, herdr-core):

- Claude workers, each in its own git worktree and its own app space;
- Codex reviewers working in parallel, one branch per reviewer;
- a merger that merges approved work, and archives or pushes only on command;
- watching the agents, cleaning up worktrees, restarting the dev server.

It fits projects that use [OpenSpec](https://github.com/Fission-AI/OpenSpec): branch = worktree = change id, workers propose and then apply the change, reviewers run `openspec validate --strict`. Merged but unarchived changes become the user's "to check" list, and a change is archived only after the user confirms it. Projects without OpenSpec work too, with plain branches.

## Install

```sh
git clone https://github.com/alexzomai/leha-loop ~/.claude/skills/leha-loop
```

Requires `hc` (herdr-core), `claude`, `codex`, `git`, `jq` and `python3`.

## Getting started in a project

In a Claude Code session inside Herdr app, in the project folder, say "you are the feature boss" (or run `/leha-loop`). The boss runs `scripts/status.sh`, creates the project config `~/.config/leha-loop/<project>.env` from `config.example.env`, and then `scripts/team-up.sh` starts the team.

Project rules for all agents live in `~/.config/leha-loop/<project>.notes.md`. General role rules live in `roles/`.

Details are in [SKILL.md](SKILL.md).
