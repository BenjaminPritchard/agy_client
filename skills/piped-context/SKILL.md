---
name: piped-context
description: Guidelines and procedures for handling external context piped into Antigravity via agy_client.
---

# Piped Context Skill

This skill activates when you receive an incoming message marked with `[Context piped via agy_client]`.

## Context Structure

Incoming messages sent via `agy_client` will follow this structure:

```markdown
[Context piped via agy_client]
• Working Directory: `/path/to/cwd`
• Instruction / User Note: <optional instruction>

```text
<piped output from external command>
```
```

## How to Handle Piped Context

1. **Acknowledge the Source**:
   - Note the working directory where the command was run in the external terminal.
   - If the external directory differs from the current workspace root, resolve relative file paths accordingly.

2. **Understand the Content**:
   - **Directory listings (`ls`, `find`, `tree`)**: Use this information to understand directory layouts or newly created files without having to execute redundant tool calls.
   - **Diffs (`git diff`, `git status`)**: Review the changes, check for syntax errors or anti-patterns, and provide immediate critique or code reviews.
   - **Logs and Errors (`tail -f`, build output, `pytest`, compiler errors`)**: Diagnose root causes, pinpoint error lines, and propose fixes or run relevant test commands.
   - **Data snippets (`curl`, `jq`, `cat`)**: Inspect data payloads or configuration files.

3. **Respond Concisely**:
   - If the user included an instruction or note (e.g., `"Review this diff"` or `"Why did this fail?"`), directly answer that question first.
   - If no note was provided (e.g., bare `ls | agy_client`), provide a brief summary of what you observe and ask how you can help.

--------------------------------------------------------------------------------

## Handling Piped Screenshots (`agy-screenshot`)

When a message is received with `[Context piped via agy-screenshot]`:
1. **Immediately Inspect the Image**:
   - Proactively call the `view_file` tool on the image file path provided in the message:
     ```python
     view_file(AbsolutePath="/path/to/screenshot.png", toolAction="Inspecting screenshot", toolSummary="Screenshot analysis")
     ```
   - The multimodal model will view the visual screenshot directly.
2. **Answer the User's Query**:
   - Analyze UI components, error dialogues, terminal traces, layout glitches, or diagrams shown in the captured rectangle.
   - Address the user's question or instruction directly and accurately.
