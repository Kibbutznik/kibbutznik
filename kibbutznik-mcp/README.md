# kibbutznik-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) server that
exposes the [Kibbutznik](https://kibbutznik.org) governance API as tools
for any MCP-compatible agent — Claude Desktop, Claude Code, Cursor, Zed,
Goose, your own host, etc.

Your AI does the reasoning locally; this server is a thin, typed wrapper
over Kibbutznik's HTTP API. You pay for your own LLM, Kibbutznik pays
for nothing on your behalf.

## Tools

| Tool | What it does |
|---|---|
| `list_my_kibbutzim` | Kibbutzim I'm a member of |
| `browse_public_kibbutzim` | Discover kibbutzim to join; supports search |
| `get_kibbutz_snapshot` | Full state dump of one community |
| `list_proposals` | Proposals in a kibbutz, optional status filter |
| `create_proposal` | File a proposal of any type (auto-submits) |
| `support_proposal` | Back a proposal (idempotent) |
| `add_comment` | Post on a proposal or chat in a community |
| `support_pulse` | Push the pulse to advance governance |
| `apply_to_join` | File a Membership proposal in a community |

## Install

```bash
pip install kibbutznik-mcp
# or from source:
git clone https://github.com/kibbutznik/kibbutznik-mcp.git
pip install -e ./kibbutznik-mcp
```

## Get an API token

1. Sign in at [kibbutznik.org/app](https://kibbutznik.org/app/)
2. Go to **Profile** → **API tokens**
3. Click **Create token**, name it (e.g. "my-claude"), copy the value
4. Save it somewhere safe — it's shown exactly once

## Configure your MCP host

### Claude Desktop / Claude Code

Add to your MCP config (e.g. `~/.config/claude-desktop/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "kibbutznik": {
      "command": "kibbutznik-mcp",
      "env": {
        "KIBBUTZNIK_API_TOKEN": "kbz_..."
      }
    }
  }
}
```

### Cursor / Zed / other MCP hosts

Same shape — point at the `kibbutznik-mcp` command and set the env var.

### Local dev (optional)

Point at a local Kibbutznik:

```json
"env": {
  "KIBBUTZNIK_API_TOKEN": "local-dev-token",
  "KIBBUTZNIK_BASE_URL": "http://localhost:8000"
}
```

## Example session

Once the server is wired up, ask your agent things like:

> What kibbutzim am I in, and what's pending in each?

> In the Reading Circle kibbutz, propose a new AddStatement saying
> "Comments must quote the text they react to."

> Support the pulse in every kibbutz I'm a member of where there are
> more than 3 open proposals.

The agent will pick the right tools, compose them, and report back.

## Security

- Every tool call authenticates with your API token in the
  `Authorization: Bearer …` header.
- Writes (`create_proposal`, `support_proposal`, `add_comment`, …) are
  ALWAYS scoped to YOUR user_id — the Kibbutznik server refuses
  requests where `body.user_id != session.user_id`.
- Tokens are server-side long-lived (1 year) but you can revoke at any
  time from Profile → API tokens.
- No password is ever part of this flow.

## License

MIT.
