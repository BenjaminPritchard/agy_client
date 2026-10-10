# Antigravity Rules for Piped Context

- When messages are received with the header `[Context piped via agy_client]`, prioritize addressing them immediately as live user context.
- When messages are received with the header `[Context piped via agy-screenshot]`, proactively call `view_file` on the captured screenshot image file to visually inspect it before responding.
- Treat the piped content as standard input from an external terminal window in the user's workflow.
- Always check the `Working Directory` reported in the piped message when interpreting relative file paths.
- Keep responses concise and focused directly on the user's intent.
