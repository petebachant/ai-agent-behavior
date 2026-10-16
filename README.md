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
repo, other runs, or the real home directory, and allows writes only inside
the run's directory, so agents cannot change the machine. Commands are
provided through one directory of symlinks to the usual system tool
directories, which lets a condition leave a command off the machine
entirely (it is also blocked from running by absolute path). Prompts read
like everyday requests, and the checks used to score the results never
enter the workspace.

**Records.** For every run, the full session is saved to
`runs/<experiment>/rep-<n>/sessions/<condition>/<agent>/` (kept in Git):
the exact prompt, the agent's streamed output (`transcript.jsonl`), its
stderr, its own native session files (`native/`), a file listing of the
final workspace, and run metadata (CLI version, model, timing, exit code).
The final workspace itself, including any Git history the agent made, is
archived to `runs/<experiment>/rep-<n>/workspaces/<condition>/<agent>.tar.gz`
(kept with DVC) for scoring, leaving out virtual environments and caches.
Everything else the run left behind is archived too, for later inspection,
to `runs/<experiment>/rep-<n>/trees/<condition>/<agent>.tar.gz` (kept with
DVC): the whole fresh home, including virtual environments, files written
outside the project directory, and the agent's own state and caches, plus
the temp dir and the sandbox profile. Copied credentials are left out, and
secrets and account identifiers are masked in every saved file. Each
repetition of each experiment is its own pipeline
stage, so more repetitions can be added by raising the range in
`calkit.yaml` without rerunning the existing ones.

**Experiments.**

- `code-quality`: the same two-feature request against a deliberately poor
  and a well-structured version of the same sales-report tool. Code quality
  before and after is measured with Ruff violation density, mean cyclomatic
  complexity, and maintainability index; hidden acceptance checks verify the
  features work. Since the request doesn't mention tests, whether agents
  add or run tests is recorded too.
- `reproducibility`: an everyday data analysis request in a directory
  holding only a CSV. The final workspace is checked for version control,
  environment management, a pipeline with hashed I/O, and defined
  provenance. In a `no-git` condition the same request is made on a machine
  without Git, and the sessions are checked for whether agents try Git,
  notice it is missing, or suggest or attempt installing it.
- `vibe-coding`: a request for recommendations on how much to vibe code at
  each stage of product development, on a fixed 0--4 scale. These results
  are the agents' opinions, not measured outcomes.

## Pre-registration

Hypotheses for each question, with supporting literature from
[`references.bib`](references.bib), were committed to `calkit.yaml` along
with the prompts, fixtures, and scoring scripts before any of the recorded
runs, so the Git history shows they were set in advance. Before that, a
pilot of one run each by Codex and OpenCode per experiment was used to test
the runner and scorers, and then discarded. Its results (on the poor
codebase both agents' changes scored as improvements; neither used version
control, declared an environment, or set up a pipeline; and both
recommended levels 4, 3, 2, 2, 1 across the stages) were seen before the
hypotheses were written, but the hypotheses are based on the cited
literature. The `unprompted-tests` and `missing-git` questions were added
afterwards, still before any recorded runs, with a further pilot of the
`no-git` condition and of Codex on the code-quality task: neither agent
added tests, Codex ran no tests, and neither agent tried or mentioned Git
when it was missing. The pilots ran OpenCode with Gemini 3.1 Pro; it
was switched to DeepSeek v4.1 Flash (the free tier on OpenCode Zen)
when the account ran out of funds partway through the first full run.
The runs made up to that point were discarded unscored and rerun, and
no pilot results exist for the model OpenCode uses in the experiments.
After the answers were first written, the code-quality runs were
redone because the well-structured fixture held stray Python cache
files (`__pycache__/`) that aren't part of it in Git; the answers in
`calkit.yaml` read their numbers from the current results, and its
notes say where the earlier set differed.

## Related work

A multivocal literature review of vibe coding
([Siddeeq et al., 2026](https://arxiv.org/abs/2607.21652), `siddeeq2026`
in [`references.bib`](references.bib)) appeared after the hypotheses were registered, so it plays no part in them; it is
compared with the results here after the fact. It synthesizes how often
claims appear across 47 sources rather than measuring behavior, so the
comparison is between what the literature says and what agents did:

- **Vibe coding by stage.** The review finds the evidence strongest for
  prototyping and weakest for production and safety-critical use, and
  advises scaling verification with the intended use of the code. The
  agents' recommendations follow the same shape, though they may simply
  echo the same literature rather than add independent evidence.
- **Tests.** Validation and testing pipelines are the safeguard the
  reviewed sources recommend most, but the agents added or ran tests
  mostly when the codebase already had them, leaving that safeguard to
  the user otherwise.
- **Reproducibility.** The review finds reproducibility, and data and ML
  work, among the least studied areas, and recommends recording prompts
  and model versions. The `reproducibility` experiment adds evidence
  there, with agents adopting none of the practices unprompted.
- **Code quality.** The review calls maintainability a conditional
  outcome and long-term quality under-studied. The `code-quality`
  experiment measures only one change with static metrics, so it speaks
  to the former but not the latter.

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
