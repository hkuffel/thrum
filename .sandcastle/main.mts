// Sequential Implement → Simplify → PR loop
//
// This template drives a three-phase workflow per issue:
//   Phase 1 (Implement): A codex agent picks an open `ready-for-agent` issue,
//                        works on it on a dedicated branch using RGR, runs the
//                        ruff feedback loop, and commits with an IMPLEMENT: prefix
//                        plus a `Refs #<n>` footer.
//   Phase 2 (Simplify):  A codex agent reduces complexity on the same branch,
//                        re-runs the checks, and commits with a SIMPLIFY: prefix
//                        (it may make no changes).
//   Phase 3 (Publish):   main.mts pushes the branch and opens a GitHub PR against
//                        main with a body assembled from the agents' <overview>
//                        and <validation> blocks, linking the issue via `Closes #N`.
//
// There is no review phase — PRs are reviewed by greptile after they open.
// Phases 1 and 2 share a single sandbox created via createSandbox(), so both
// agents work on the same explicit branch.
//
// The outer loop repeats up to MAX_ITERATIONS times, processing one issue per
// iteration.
//
// Usage:
//   npx tsx .sandcastle/main.mts

import * as sandcastle from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { execFileSync } from "node:child_process";
import { writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Maximum number of implement→simplify→PR cycles to run before stopping.
// Each cycle works on one issue. Raise this to process more issues per run.
const MAX_ITERATIONS = 1;

// Sandcastle's default per-hook timeout is 60s. The first `uv sync` downloads
// the Python toolchain and builds the project, which regularly exceeds that.
// Give it 10 minutes.
const HOOK_TIMEOUT_MS = 600_000;

// Agent provider/model used for all Sandcastle phases.
// Pi supports thinking-level shorthand in the model string, e.g. `:<level>`.
// If this exact OpenAI/Codex model is not available in your pi install, run
// `pi --list-models gpt-5.5` and update this value to the matching model ID.
const AGENT_MODEL = "openai-codex/gpt-5.5:low";

const sandboxProvider = docker({
    mounts: [
        {
            // Reuse the host pi `/login` credentials inside the sandbox.
            // pi stores auth/config here by default and expects the same path
            // under the sandbox user's home directory.
            hostPath: "~/.pi/agent",
            sandboxPath: "/home/agent/.pi/agent",
            // pi creates lock/session/runtime files and may refresh OAuth tokens.
            // Mount this read-write; a read-only mount makes pi fail before it
            // can load the host login (EROFS on settings.json.lock).
            readonly: false,
        },
    ],
});

// Hooks run inside the sandbox before the agent starts each iteration.
// thrum is a uv project: `uv sync` installs the package plus the dev group
// (ruff, pytest) the agent needs for the feedback loop.
const hooks = {
    sandbox: {
        onSandboxReady: [
            {
                command: "uv sync",
                timeoutMs: HOOK_TIMEOUT_MS,
            },
        ],
    },
};

// Nothing to copy from the host: the only heavy dependency tree is the Python
// .venv, and we deliberately do NOT copy it — uv creates platform-specific
// symlinks to the host Python interpreter, which break inside the Linux
// sandbox. `uv sync` rebuilds it cleanly in the hook above.
const copyToWorktree: string[] = [];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Extract the inner text of a tag (e.g., <overview>…</overview>) from an
// agent's stdout. Returns null if the tag is absent so callers can fall back
// to a placeholder rather than dropping the section silently.
function extractTag(stdout: string, tag: string): string | null {
    const match = stdout.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`));
    return match ? match[1]!.trim() : null;
}

// Assemble the final PR body from the implement and simplify phases' structured
// blocks. Each section degrades independently: a missing block renders as a
// visible placeholder rather than collapsing the whole body to boilerplate.
function assemblePrBody(args: {
    issueNumber: number;
    implementStdout: string;
    simplifyStdout: string;
}): string {
    const { issueNumber, implementStdout, simplifyStdout } = args;

    const overview =
        extractTag(implementStdout, "overview") ?? "_Overview not generated._";
    const implementValidation =
        extractTag(implementStdout, "validation") ??
        "_No validation reported by implementer._";
    const simplifyValidation =
        extractTag(simplifyStdout, "validation") ??
        "_No validation reported by simplifier._";

    return [
        "## Overview",
        overview,
        "",
        "## Validation",
        "**Implementation:**",
        implementValidation,
        "",
        "**Simplification:**",
        simplifyValidation,
        "",
        `Closes #${issueNumber}`,
    ].join("\n");
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

for (let iteration = 1; iteration <= MAX_ITERATIONS; iteration++) {
    console.log(`\n=== Iteration ${iteration}/${MAX_ITERATIONS} ===\n`);

    // Generate a unique branch name for this iteration. The implementer
    // self-selects the issue, so the issue number isn't known yet.
    const branch = `sandcastle/${Date.now()}`;

    // Create a single sandbox that both the implementer and simplifier share.
    // This gives both agents a real, named branch that persists across phases.
    const sandbox = await sandcastle.createSandbox({
        branch,
        sandbox: sandboxProvider,
        hooks,
        copyToWorktree,
    });

    try {
        // -------------------------------------------------------------------
        // Phase 1: Implement
        //
        // A codex agent picks the next open `ready-for-agent` issue, writes the
        // implementation (RGR: Red → Green → Repeat → Refactor), runs ruff, and
        // commits the result with a `Refs #<n>` footer.
        // -------------------------------------------------------------------
        const implement = await sandbox.run({
            name: "implementer",
            maxIterations: 100,
            agent: sandcastle.pi(AGENT_MODEL),
            promptFile: "./.sandcastle/implement-prompt.md",
        });

        if (!implement.commits.length) {
            console.log(
                "Implementation agent made no commits. Skipping simplify + PR.",
            );
            continue;
        }

        console.log(`\nImplementation complete on branch: ${branch}`);
        console.log(`Commits: ${implement.commits.length}`);

        // -------------------------------------------------------------------
        // Phase 2: Simplify
        //
        // A second codex agent reduces complexity on the same branch while
        // preserving exact functionality. It may make no changes.
        // -------------------------------------------------------------------
        const simplify = await sandbox.run({
            name: "simplifier",
            maxIterations: 25,
            agent: sandcastle.pi(AGENT_MODEL),
            promptFile: "./.sandcastle/simplify-prompt.md",
            promptArgs: {
                BRANCH: branch,
            },
        });

        console.log("\nSimplify complete.");

        // -------------------------------------------------------------------
        // Phase 3: Publish
        //
        // Push the branch and open a PR against main. The issue number is
        // recovered from the `Refs #<n>` footer the implementer wrote, so the
        // PR can link it with `Closes #<n>`. No merge, no issue-tracker calls.
        // -------------------------------------------------------------------
        const commitBodies = execFileSync(
            "git",
            ["log", `main..${branch}`, "--format=%B"],
            { encoding: "utf8" },
        );
        const refsMatch = commitBodies.match(/Refs #(\d+)/);
        if (!refsMatch) {
            console.error(
                "No `Refs #<n>` footer found in commits; cannot link an issue. " +
                    "Skipping PR — push the branch and open it manually.",
            );
            continue;
        }
        const issueNumber = Number(refsMatch[1]);

        const issueTitle = execFileSync(
            "gh",
            ["issue", "view", String(issueNumber), "--json", "title", "-q", ".title"],
            { encoding: "utf8" },
        ).trim();

        execFileSync("git", ["push", "-u", "origin", branch], {
            stdio: "inherit",
        });

        const bodyFile = join(tmpdir(), `pr-body-${branch.replace(/\//g, "-")}.md`);
        writeFileSync(
            bodyFile,
            assemblePrBody({
                issueNumber,
                implementStdout: implement.stdout,
                simplifyStdout: simplify.stdout,
            }),
        );

        let prUrl: string;
        try {
            prUrl = execFileSync(
                "gh",
                [
                    "pr",
                    "create",
                    "--base",
                    "main",
                    "--head",
                    branch,
                    "--title",
                    `${issueTitle} (#${issueNumber})`,
                    "--body-file",
                    bodyFile,
                ],
                { encoding: "utf8" },
            ).trim();
        } finally {
            try {
                unlinkSync(bodyFile);
            } catch {
                /* ignore */
            }
        }

        execFileSync(
            "gh",
            ["issue", "comment", String(issueNumber), "--body", `Opened PR: ${prUrl}`],
            { stdio: "inherit" },
        );

        console.log(`\nPull request opened: ${prUrl}`);
    } finally {
        await sandbox.close();
    }
}

console.log("\nAll done.");
