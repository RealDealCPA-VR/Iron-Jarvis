"""The Jarvis-owned Pi adapter (Q01): a stdio bridge Jarvis writes, not a package.

Q01 resolved two things at once, and this module exists to honour both.

**Pi core is not assumed to consume MCP.** So Jarvis owns the bridge:

.. code-block:: text

    Pi
     |
     v
    Jarvis-owned Pi adapter   <- this module writes it
     |
     v
    Jarvis POST /mcp
     |
     v
    pane-scoped capability token

**And nothing in the Browser architecture may depend on it.** The adapter is
placed by :class:`iron_jarvis.terminals.recipes.PiRecipe` and named in that
recipe's ``config_writes``; if a later Pi advertises native MCP in its own help,
``PiRecipe.supports`` returns ``mcp_http`` and this file is never written. No
third-party Pi MCP package is imported, installed, or required — by Jarvis or by
the adapter, which is plain Node with no dependencies at all.

**The runtime is resolved the way ``ai_clis`` already resolves Pi.**
:func:`pi_runtime` calls ``ai_clis._find``, whose Windows fallbacks include
``%LOCALAPPDATA%/pi-node/current`` — the Node that Pi's own installer ships and
that a GUI-launched daemon's inherited PATH never sees. That fallback exists
*because* of Pi (see ``_extra_bin_dirs``), so reusing it is the least invasive
attachment point rather than a second discovery path that could disagree with the
first.

**No credential is written to disk.** The script reads ``IRONJARVIS_MCP_URL`` and
``IRONJARVIS_MCP_TOKEN`` from its own environment, which the pane's shell already
carries because the recipe put them in ``pane_env`` before the spawn. A token
baked into a file in the user's project folder would outlive the pane that owns
it; one read from the environment dies with the process, which is the property
:mod:`iron_jarvis.browser.panetokens` is built around.

**One request at a time, in order.** The stdin handler is ``async``, and Node
delivers the next ``data`` event while the previous handler is suspended at its
first ``await`` - so two overlapping handlers relayed concurrently: request 2
reached ``/mcp`` BEFORE request 1's response had produced the ``Mcp-Session-Id``,
went out with no session header, and was refused, while the answers came back out
of order. Every line is therefore appended to ONE promise chain, so
``initialize`` has set ``sessionId`` before the next request is built. For the
same reason ``end`` does not exit: it marks stdin closed and exits only after the
chain has drained. ``process.exit(0)`` inside the ``end`` handler killed every
in-flight request - a client that wrote three lines and closed stdin got total
silence and the server saw nothing at all.

**What the adapter is honest about.** It speaks line-delimited JSON-RPC on stdio
and relays each request to ``POST /mcp`` with the pane token, reading back either
``application/json`` or ``text/event-stream`` bodies — the same two shapes this
repository's own client (``mcp/client.py``) already parses, and the same
``Mcp-Session-Id`` header, captured from ``initialize`` and echoed on every later
request. It adds no tools, filters nothing, and invents no responses: a transport
failure comes back as a JSON-RPC error naming what failed, because a bridge that
fabricated a result would put a lie inside the one path this ship exists to keep
honest.
"""

from __future__ import annotations

from .ai_clis import _find

#: The file the recipe writes into the pane's working directory. ``.mjs`` so Node
#: reads it as an ES module regardless of any ``package.json`` in the folder —
#: a project with ``"type": "commonjs"`` would otherwise refuse the ``import``s.
ADAPTER_FILENAME = "jarvis-pi-adapter.mjs"

#: The environment variables the adapter reads. Spelled here as literals rather
#: than imported so this module stays importable with nothing else loaded; pinned
#: equal to :mod:`iron_jarvis.browser.panetokens` by
#: ``tests/test_launch_recipes_v1238.py``, because drift would leave the adapter
#: reading a variable nobody sets and reporting "no token" forever.
URL_ENV = "IRONJARVIS_MCP_URL"
TOKEN_ENV = "IRONJARVIS_MCP_TOKEN"

#: Node candidates, in order. ``_find`` searches the real PATH first and then the
#: per-user bin dirs, so this reaches Pi's bundled runtime on a machine where the
#: daemon's PATH predates Pi's installer.
_RUNTIME_CANDIDATES = ("node", "node.exe")


def pi_runtime() -> str:
    """The Node executable the adapter would run under, or ``""`` if none.

    ``""`` is an ordinary answer, not an error: :class:`PiRecipe` turns it into a
    user-readable limitation and the pane launches exactly as it does today.
    """
    for candidate in _RUNTIME_CANDIDATES:
        found = _find(candidate)
        if found:
            return found
    return ""


_ADAPTER_JS = '''\
#!/usr/bin/env node
// Iron Jarvis <-> Pi adapter. Written by Iron Jarvis for one Build pane.
// Safe to delete; the pane will write it again on the next launch.
//
// stdio (line-delimited JSON-RPC, which is what Pi can be pointed at) on one
// side; POST {URL_ENV} with the pane token on the other. No dependencies, and
// no credential in this file: both come from the environment the pane's shell
// already carries.

const URL_ENV = "{URL_ENV}";
const TOKEN_ENV = "{TOKEN_ENV}";

const endpoint = process.env[URL_ENV] || "";
const token = process.env[TOKEN_ENV] || "";

let sessionId = "";

function fail(id, message) {{
  // A transport failure is reported as a JSON-RPC error, never as a fabricated
  // result: the whole point of routing Pi through Jarvis is that what comes
  // back is what actually happened.
  return {{ jsonrpc: "2.0", id: id === undefined ? null : id, error: {{ code: -32603, message }} }};
}}

function parseBody(text, contentType) {{
  if ((contentType || "").includes("text/event-stream")) {{
    // SSE: the JSON-RPC payload rides on `data:` lines, same as the Iron Jarvis
    // MCP client parses.
    for (const raw of String(text).split(/\\r?\\n/)) {{
      const line = raw.trim();
      if (line.startsWith("data:")) {{
        const body = line.slice(5).trim();
        if (body) return JSON.parse(body);
      }}
    }}
    return null;
  }}
  const trimmed = String(text || "").trim();
  return trimmed ? JSON.parse(trimmed) : null;
}}

async function relay(request) {{
  if (!endpoint) return fail(request.id, `${{URL_ENV}} is not set: launch this pane from Iron Jarvis Build`);
  if (!token) return fail(request.id, `${{TOKEN_ENV}} is not set: launch this pane from Iron Jarvis Build`);
  const headers = {{
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    authorization: `Bearer ${{token}}`,
  }};
  if (sessionId) headers["mcp-session-id"] = sessionId;
  let response;
  try {{
    response = await fetch(endpoint, {{ method: "POST", headers, body: JSON.stringify(request) }});
  }} catch (err) {{
    return fail(request.id, `could not reach Iron Jarvis at ${{endpoint}}: ${{err && err.message ? err.message : err}}`);
  }}
  const sid = response.headers.get("mcp-session-id");
  if (sid) sessionId = sid;
  const text = await response.text();
  if (!response.ok) {{
    let detail = text;
    try {{ const j = JSON.parse(text); if (j && j.detail) detail = j.detail; }} catch (_) {{}}
    return fail(request.id, `Iron Jarvis refused this call (HTTP ${{response.status}}): ${{detail}}`);
  }}
  try {{
    return parseBody(text, response.headers.get("content-type"));
  }} catch (err) {{
    return fail(request.id, `Iron Jarvis sent a body this adapter could not read: ${{err && err.message ? err.message : err}}`);
  }}
}}

async function handle(line) {{
  let request;
  try {{
    request = JSON.parse(line);
  }} catch (err) {{
    process.stdout.write(JSON.stringify({{ jsonrpc: "2.0", id: null, error: {{ code: -32700, message: "parse error" }} }}) + "\\n");
    return;
  }}
  const answer = await relay(request);
  // A notification carries no id and must get no response body, or the
  // handshake on the other side never completes.
  if (answer !== null && request && Object.prototype.hasOwnProperty.call(request, "id")) {{
    process.stdout.write(JSON.stringify(answer) + "\\n");
  }}
}}

// ONE chain, not one handler per data event: `relay` is async, so two
// overlapping handlers would send request 2 before request 1's response had
// captured the session id, and would print the answers in whichever order the
// server happened to finish.
let queue = Promise.resolve();
let ended = false;

function enqueue(line) {{
  queue = queue.then(() => handle(line)).catch(() => {{}});
  return queue;
}}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {{
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\\n")) >= 0) {{
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (line) enqueue(line);
  }}
}});

process.stdin.on("end", () => {{
  // Drain first. Exiting here discarded every request still in flight, so a
  // client that wrote and then closed stdin got silence instead of answers.
  if (ended) return;
  ended = true;
  const rest = buffer.trim();
  buffer = "";
  if (rest) enqueue(rest);
  queue.then(() => process.exit(0), () => process.exit(0));
}});
'''


def adapter_source() -> str:
    """The adapter script, as text.

    A function rather than a constant so the two environment-variable names are
    substituted from :data:`URL_ENV` / :data:`TOKEN_ENV` in exactly one place —
    a script with a hand-typed variable name in it would read an environment
    variable nothing sets and fail with a message about a missing token.
    """
    return _ADAPTER_JS.format(URL_ENV=URL_ENV, TOKEN_ENV=TOKEN_ENV)
