# TASK

Open a pull request for branch `{{BRANCH}}` against `{{SOURCE_BRANCH}}` with a concise, accurate description.

# CONTEXT

## Branch diff

!`git diff {{SOURCE_BRANCH}}...{{BRANCH}}`

## Commits on this branch

!`git log {{SOURCE_BRANCH}}..{{BRANCH}} --oneline`

## Commit messages (for issue reference)

!`git log {{SOURCE_BRANCH}}..{{BRANCH}} --format='%B'`

## Detected PR description templates

!`ls .github/pull_request_template.md .github/PULL_REQUEST_TEMPLATE.md .github/PULL_REQUEST_TEMPLATE/*.md 2>/dev/null || true`

# PROCESS

1. **Push the branch** to the remote so a PR can be opened:

   ```
   git push -u origin {{BRANCH}}
   ```

2. **Find the issue number.** The implement phase records a `Refs #<n>` footer in its
   commit message(s). Read the commit messages above and extract that number. If no
   `Refs #<n>` is present, fall back to inferring the issue from the commit subjects.

3. **Compose the description.**
   - **If a template file was listed** under "Detected PR description templates": read it
     and fill out every section faithfully, preserving the template's headings and order.
     Do not invent sections or drop required ones.
   - **Otherwise**, write a concise default body:
     - A 1–2 sentence summary of *what changed and why*.
     - A short bullet list of the key changes.
     - No filler, no restating the diff line by line.

4. **Link the issue.** Append a footer line `Closes #<n>` (using the number from step 2)
   so merging the PR closes the issue. If you have a template that already has a place for
   issue links, put it there instead of duplicating.

5. **Create the PR** (ready for review — not a draft). Write the composed body to a temp
   file first to avoid shell-quoting problems, then:

   ```
   gh pr create --base {{SOURCE_BRANCH}} --head {{BRANCH}} --title "<concise title>" --body-file <tmpfile>
   ```

   If a PR already exists for this branch, update it instead of failing:

   ```
   gh pr edit {{BRANCH}} --title "<concise title>" --body-file <tmpfile>
   ```

# RULES

- Keep the title concise and imperative (e.g. `Add durable job retry backoff`).
- Never push to or open a PR against any branch other than `{{BRANCH}}` → `{{SOURCE_BRANCH}}`.
- Do not modify code in this phase — only push and open/update the PR.

# DONE

Once the pull request is open (or updated), output the completion signal:

<promise>COMPLETE</promise>
