/** Where other assistants reach this site's MCP endpoint (QRY-003). */
export function mcpAddress(): string {
  return `${window.location.origin}/mcp`
}

/** The settings most MCP clients read (Claude Desktop, Claude Code, Cursor and others). */
export function clientConfig(key: string, address: string = mcpAddress()): string {
  return JSON.stringify(
    {
      mcpServers: {
        'novel-engine': {
          type: 'http',
          url: address,
          headers: { Authorization: `Bearer ${key}` },
        },
      },
    },
    null,
    2,
  )
}
