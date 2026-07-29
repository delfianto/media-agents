---
name: stash-app
description: Your ultimate, unashamed partner in crime for organizing, tagging, and managing your entire collection of NSFW media on your local Stash server. Seamlessly interacts with performers, scenes, and tags via MCP because a well organized stash is a happy stash.
---

# Stash App

This skill provides a multi-subcommand interface for interacting with and managing a local Stash instance via the `stash-mcp` server.

## Pre-requisite Connectivity Check
For any operation using this skill, the model MUST first verify connectivity to the Stash server.
1. Run the `health_check` tool on the `stash-mcp` server.
2. If `health_check` is not available, fails to run, or returns a failure response, **SKIP all processing immediately**. Do not run any other tools or search the web. Inform the user clearly that: `The Stash server is not reachable through MCP. Please make sure the stash-mcp server is active and configured correctly.`

---

## Part I — Sub-Commands

When the user's message arrives, parse it for the recognized sub-command: **performer**. Match case-insensitively and accept close variants (e.g. "performer data", "performer"). If no sub-command is detected, fall through to the standard interaction.

### Dispatch: Gathering Context Before Acting

When the sub-command is detected but the target performer is not explicit, ask the user directly to collect the minimum needed context. Ask only what is genuinely missing.

- "Which performer's profile do you want?" — if no name was provided for `performer`.

---

### `performer`

**Purpose.** Retrieves a structured profile of a performer from the Stash database.

**Procedure:**

1. **Connectivity Check:** Run the `health_check` pre-requisite. Stop and report if it fails.
2. **Fetch Performer Data:** Call the `get_performer_info` tool with the `performer_name`. For additional details, optionally call `advanced_performer_analysis` with `performer_name`.
3. **Format and Present:**
    - **JSON Output (Default):** Show the raw JSON response returned by the `get_performer_info` tool.
    - **Markdown Output:** If the user explicitly asks for markdown, human-readable data, or a table, format the performer's profile details (e.g., name, age, height, eye color, scenes, tags) in a clean, readable Markdown table.

---

### Fallthrough

If the user's instruction does not map to any of the sub-commands above, treat it as a standard request.

**Requirements & Constraints for all operations:**

- Do not attempt to run any query tools if the `health_check` check fails.
- Only use tags that exist in the stash database. Do not attempt to create new tags.
