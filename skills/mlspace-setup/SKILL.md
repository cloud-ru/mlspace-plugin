---
name: mlspace-setup
description: Configure an installed MLSpace plugin, connect Cloud.ru credentials, choose workspaces, or recover an incomplete installation. Use for setup requests or missing credentials, not ordinary workload operations.
---

# Configure MLSpace

Use the installation instructions from the same release as this skill.

For a native Claude Code/Codex plugin, derive its root from the absolute path of
this file: `<root>/skills/mlspace-setup/SKILL.md`. Check that the root contains
`pyproject.toml` and `src/mlspace_mcp/__main__.py`, then read `<root>/INSTALL_AGENT.md`.
Do not infer a plugin path from a working directory or invent a cache path.

Give the person this command with the actual, shell-quoted absolute root:

```sh
uvx --python ">=3.10" --from '/absolute/path/to/installed/plugin' mlspace-plugin setup --config-only
```

They run it in their own terminal (including SSH), enter Key ID/Secret under
asterisks and choose workspaces. Add `--force` for an explicitly requested
reconfiguration. The plugin is already installed; `--config-only` avoids
installing it again or connecting unrelated clients. Do not read credentials,
request them in chat, or place their values in commands.

An OpenCode copy of this skill is outside the runtime. Do not treat the OpenCode
skills directory as the package root. Use the original installation command and
its `INSTALL_AGENT.md`; if that source is unknown, obtain the repository or
installation path from the person before constructing a command.

After setup, have the person reload/restart the client. Call `mlspace_contexts`
to confirm the connected workspaces. A successful setup alone does not prove the
current agent session has loaded the connection. For an error, use the sanitized
diagnostic log path reported by setup, never the credentials file.
