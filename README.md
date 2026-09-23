# enfocus-switch-api

Example PHP project for communicating with Enfocus Switch API

1. Class using curl_exec
2. Class using Guzzle Http Client

## Installing this bundle

Clone this repo to your local drive or download and unpack zip.

Open terminal and go to the project folder

The recommended way to install this bundle with dependencies is through
[Composer](http://getcomposer.org).

```bash
php composer.phar update | composer install
```

## AI connector (MCP server)

[`switch-mcp/`](switch-mcp) contains an MCP server that lets AI assistants (Claude Desktop,
Claude Code, …) track jobs, triage checkpoints, approve/route jobs (opt-in) and explain
PitStop preflight reports in plain language for customers, CSRs and prepress.
See [`switch-mcp/README.md`](switch-mcp/README.md) and the research notes in
[`docs/ai-connector-research.md`](docs/ai-connector-research.md).
