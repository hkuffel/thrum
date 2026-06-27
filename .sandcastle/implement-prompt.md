# Context

## Open issues ready for an agent

!`gh issue list --state open --label ready-for-agent --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`

## Recent commits (last 10)

!`git log --oneline -10`

# Task

You are an autonomous coding agent working through issues one at a time.

## Pick an issue

From the open `ready-for-agent` issues above, pick the highest-priority one that
is not blocked by another open issue. Skip any issue that already has an open
pull request linked to it (check with `gh pr list --state open --search "<n>"`
if unsure). Prefer, in order:

1. **Bug fixes** — broken behaviour affecting users
2. **Tracer bullets** — thin end-to-end slices that prove an approach works
3. **Polish** — improving existing functionality (error messages, UX, docs)
4. **Refactors** — internal cleanups with no user-visible change

Pull in the full issue with `gh issue view <number> --comments`. If it
references a parent PRD, pull that in too. Work on **one issue only**.

# Exploration

Read the relevant source files and tests before writing any code. Read
`CONTEXT.md` for the domain language and architecture, the ADRs under
`docs/adr/` if your change touches a previously-decided area, and
`.sandcastle/CODING_STANDARDS.md` for conventions.

# Execution

If applicable, use RGR to complete the task:

1. RED: write one failing test
2. GREEN: write the implementation to pass that test
3. REPEAT until done
4. REFACTOR the code

Keep the change as small as possible.

# Feedback loops

thrum is a uv project at the repo root. Run these from the root:

- `uv run ruff check .` (lint)
- `uv run ruff format .` (if you touched formatting)
- `uv run pytest` (tests)

All checks must pass before you commit. Note: the test suite uses
`testcontainers[postgres]`, which needs a Docker daemon. If Docker is not
available in this sandbox, the DB-backed tests will not run — say so explicitly
in the validation block rather than inventing a result; those tests are
validated by CI on the PR.

# Commit

Make a single git commit. The message MUST:

1. Start with the `IMPLEMENT:` prefix
2. Include the task completed and any PRD reference
3. List key decisions made
4. List files changed
5. Note any blockers for the next iteration
6. End with a footer line `Refs #<issue-number>` so the PR phase can recover the issue

Do NOT close the issue — the PR phase links it with `Closes #<n>` and GitHub
closes it on merge. You may leave a brief progress comment with
`gh issue comment <number> --body "..."` if useful.

# Overview report

Output an `<overview>` block summarising the change for the PR description:

<overview>
**What:** <what changed, derived from the final diff>
**Why:** <the motivation, derived from the issue and PRD>
**How:** <the approach taken — the shape of the change>
</overview>

# Validation report

Before finishing, output a `<validation>` block listing the exact checks you ran
and their results. List only checks you actually ran — do not invent or
generalize. Use the command name and outcome, one per line:

<validation>
- `uv run ruff check .` — passing
- `uv run pytest` — passing (42 passed)
</validation>

If a check failed and you could not fix it, say so explicitly and note it as a
blocker.

# Done

When the issue is implemented and committed, output the completion signal:

<promise>COMPLETE</promise>

# Final rules

ONLY WORK ON A SINGLE TASK.
