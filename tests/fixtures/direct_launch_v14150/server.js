// A tiny stdio MCP server in plain Node (no packages, no network) for the
// v1.415.0 direct-launch pins. It is placed in a FAKE npx cache by the tests,
// exactly where npx keeps a package, and started as `node <this file>`.
//
// Behaviour by argv / env (the tests choose):
//   --exit-now     exit 3 at once without answering (a direct start that fails)
//   --never-answer read stdin and never answer (a direct start that hangs)
// Tools: `whoami` answers {cwd, argv, path0, marker, pid} so a test can prove the
// server got the same cwd, args and env a launcher start would have given it.
"use strict";
const args = process.argv.slice(2);
if (args.includes("--exit-now")) {
  process.stderr.write("direct server refused to start\n");
  process.exit(3);
}
const hang = args.includes("--never-answer");
const path = require("path");
const send = (o) => process.stdout.write(JSON.stringify(o) + "\n");
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line || hang) continue;
    let msg;
    try { msg = JSON.parse(line); } catch (e) { continue; }
    if (msg.id === undefined || msg.id === null) continue;
    if (msg.method === "initialize") {
      send({ jsonrpc: "2.0", id: msg.id, result: {
        protocolVersion: "2024-11-05", capabilities: { tools: {} },
        serverInfo: { name: "direct-fixture", version: "1.0.0" } } });
    } else if (msg.method === "tools/list") {
      send({ jsonrpc: "2.0", id: msg.id, result: { tools: [
        { name: "whoami", description: "who am i", inputSchema: { type: "object", properties: {} } },
        { name: "ping_tool", description: "ping", inputSchema: { type: "object", properties: {} } },
      ] } });
    } else if (msg.method === "tools/call") {
      const pathVar = process.env.PATH || process.env.Path || "";
      const info = {
        cwd: process.cwd(),
        argv: args,
        path0: pathVar.split(path.delimiter)[0],
        marker: process.env.IJ_DIRECT_MARKER || "",
        pid: process.pid,
        exe: process.execPath,
      };
      send({ jsonrpc: "2.0", id: msg.id, result: {
        content: [{ type: "text", text: JSON.stringify(info) }] } });
    } else {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: "no" } });
    }
  }
});
process.stdin.on("end", () => process.exit(0));
