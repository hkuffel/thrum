# TASK

Simplify the code changes on branch `{{BRANCH}}`. Reduce complexity and improve
clarity while preserving **exact** functionality. This is the last phase that
edits code — the pull request opens straight after you.

# CONTEXT

## Branch diff

!`git diff main...{{BRANCH}}`

## Commits on this branch

!`git log main..{{BRANCH}} --oneline`

# WHAT TO SIMPLIFY

Look for opportunities to:

- Reduce unnecessary complexity and nesting
- Eliminate redundant code and abstractions
- Improve readability through clear variable and function names
- Consolidate related logic
- Remove unnecessary comments that describe obvious code
- Choose clarity over brevity — explicit code is often better than overly compact code

# MAINTAIN BALANCE

Avoid over-simplification that could:

- Reduce code clarity or maintainability
- Create overly clever solutions that are hard to understand
- Combine too many concerns into single functions or components
- Remove helpful abstractions that improve code organization
- Make the code harder to debug or extend

Apply the project coding standards in @.sandcastle/CODING_STANDARDS.md.

# PRESERVE FUNCTIONALITY

Never change what the code does — only how it does it. All original features,
outputs, and behaviors must remain intact.

# EXECUTION

If you find improvements to make:

1. Make the changes directly on this branch.
2. Run the feedback loops to confirm nothing broke (from the repo root):
   `uv run ruff check .` and `uv run pytest`. If you touched formatting, also
   `uv run ruff format .`.
3. If a check fails, fix it before committing.
4. Commit with a message starting with the `SIMPLIFY:` prefix, describing the refinements.

If the code is already clean and well-structured, make no changes and no commit.

# VALIDATION REPORT

Output a `<validation>` block listing the checks you re-ran after simplifying and
their results. List only checks you actually ran. Note that the DB-backed tests
need a Docker daemon; if it is unavailable in this sandbox, say so rather than
inventing a result.

<validation>
- `uv run ruff check .` — passing
- `uv run pytest` — passing (42 passed)
</validation>

If you made no changes, emit:

<validation>
No simplifications warranted — implementation was already clean.
</validation>

Once complete, output <promise>COMPLETE</promise>.
