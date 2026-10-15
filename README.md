# AI agent behavior

Experiments to test AI agent behavior.

Coding agents are treated as subjects of an experiment: each is given an
ordinary-looking task in an ordinary-looking project directory, with nothing
indicating it is being studied, and what it does is recorded and scored.
The project's research questions and their evidence-backed answers are in
[`calkit.yaml`](calkit.yaml).

## Method

**Agents.** Each agent CLI is run with its non-interactive mode, with the
models pinned in [`agents/agents.yaml`](agents/agents.yaml):

| Agent      | Invocation                                                   |
| ---------- | ------------------------------------------------------------ |
| Claude Code | `claude -p ... --output-format stream-json --verbose`        |
| Codex      | `codex exec --json ...`                                      |
| OpenCode   | `opencode run --format json ...`                             |

**Isolation and blinding.** Every run (each agent, condition, and repeat)
gets a brand new home directory with only the agent's credentials and a Git
identity, so no memory, user-level instructions, or prior sessions carry
over. The task's files are copied into `~/projects/<name>` and the agent
starts there. The agent and everything it launches run under the macOS
Seatbelt profile in [`agents/sandbox.sb`](agents/sandbox.sb), which makes
the contents of `/Users`, `/Volumes`, and the shared temp directories
unreadable apart from the run's own directory, so agents cannot see this
repo, other runs, or the real home directory. Prompts read like everyday
requests, and the checks used to score the results never enter the
workspace.

**Records.** For every run, the full session is saved to
`runs/<experiment>/sessions/<condition>/<agent>/rep-<n>/` (kept in Git):
the exact prompt, the agent's streamed output (`transcript.jsonl`), its
stderr, its own native session files (`native/`), a file listing of the
final workspace, and run metadata (CLI version, model, timing, exit code).
The final workspace itself, including any Git history the agent made, is
archived to `runs/<experiment>/workspaces/` (kept with DVC).

**Experiments.**

- `code-quality`: the same two-feature request against a deliberately poor
  and a well-structured version of the same sales-report tool. Code quality
  before and after is measured with Ruff violation density, mean cyclomatic
  complexity, and maintainability index; hidden acceptance checks verify the
  features work.
- `reproducibility`: an everyday data analysis request in a directory
  holding only a CSV. The final workspace is checked for version control,
  environment management, a pipeline with hashed I/O, and defined
  provenance.
- `vibe-coding`: a request for recommendations on how much to vibe code at
  each stage of product development, on a fixed 0--4 scale. These results
  are the agents' opinions, not measured outcomes.

## Running

Requires macOS (for `sandbox-exec`), the agent CLIs at the versions in
`calkit.yaml`, Codex and OpenCode logged in, and a Claude Code token from
`claude setup-token` in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`. Then:

```sh
calkit run
```

Agent runs are expensive and nondeterministic, so they are cached like any
other stage and rerun only when the runner, sandbox, agent config, or an
experiment's prompt or fixtures change.
