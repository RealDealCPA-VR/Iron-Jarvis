# IRON JARVIS

## Comprehensive Product Specification

### Continuation of Master Architecture

---

# 10. Browser Session Vault

Iron Jarvis maintains encrypted browser session storage for subscription-based providers.

The vault stores:

* cookies
* local storage
* session storage
* browser fingerprints
* provider metadata

The vault never stores:

* plaintext passwords
* MFA codes
* authentication secrets outside secure storage

Storage options:

```text
OS Keychain
Windows Credential Manager
macOS Keychain
Linux Secret Service
Encrypted Local Vault
```

Directory structure:

```text
.ironjarvis/browser/

claude/
chatgpt/
codex/
grok/
gemini/
```

All vault data is encrypted at rest.

---

# 11. Agent System

Agents are the fundamental execution unit.

Every agent consists of:

```text
Identity
Capabilities
Provider
Memory
Permissions
Tools
Skills
Policies
```

## Agent Types

### Planner Agent

Responsible for:

* task decomposition
* strategy generation
* delegation
* coordination

### Builder Agent

Responsible for:

* implementation
* coding
* file modification

### Reviewer Agent

Responsible for:

* validation
* quality assurance
* risk analysis

### Research Agent

Responsible for:

* web research
* knowledge acquisition
* synthesis

### Memory Agent

Responsible for:

* memory maintenance
* summarization
* knowledge organization

### Automation Agent

Responsible for:

* workflow execution
* scheduled tasks
* integrations

---

# 12. Multi-Agent Orchestration

Iron Jarvis supports hierarchical agent orchestration.

Architecture:

```text
User
 │
 ▼
Supervisor Agent
 │
 ├── Planner
 ├── Builder
 ├── Reviewer
 ├── Researcher
 ├── Memory
 └── Automation
```

Subagents:

* operate independently
* maintain isolated context
* possess independent providers
* return summarized results

Subagents cannot directly communicate with users.

All communication flows through supervisors.

---

# 13. Agent Lifecycle

```text
Create Session
Load Context
Load Memory
Plan
Execute
Observe
Evaluate
Delegate
Refine
Finalize
Review
Persist
Complete
```

Lifecycle states:

```text
created
initializing
running
waiting
paused
delegating
reviewing
completed
cancelled
failed
```

---

# 14. Session Architecture

A session represents a unit of work.

Examples:

```text
Build OAuth support
Review tax return workflow
Analyze financial statements
Research regulations
Generate report
```

Each session contains:

```text
session id
project id
agent assignments
provider assignments
transcript
tool history
memory references
workspace state
artifacts
```

---

# 15. Workspace Architecture

Each session receives its own isolated workspace.

Examples:

```text
.ironjarvis/workspaces/

session-001/
session-002/
session-003/
```

Workspace contents:

```text
cloned repository
temporary files
artifacts
generated documents
tool outputs
logs
```

Workspaces are disposable.

Permanent changes require approval.

---

# 16. Sandbox Manager

The Sandbox Manager is responsible for safe execution.

Supported runtimes:

```text
Docker
Podman
Native Execution
Remote Containers
Future VM Support
```

Capabilities:

```text
filesystem isolation
network controls
resource limits
process limits
timeout enforcement
stdout capture
stderr capture
```

---

# 17. Sandbox Security Policies

Default policy:

```json
{
  "filesystem": "workspace_only",
  "internet": "ask",
  "process_spawn": "allow",
  "delete_files": "ask",
  "modify_env": "deny",
  "host_access": "deny"
}
```

Resource limits:

```text
CPU limits
RAM limits
disk limits
execution timeout
network restrictions
```

---

# 18. Tool System

Tools are executable capabilities.

Built-in tools include:

```text
read_file
write_file
edit_file
list_files
grep
search_codebase
shell
git_status
git_diff
git_commit
memory_read
memory_write
web_search
browser_use
mcp_call
create_document
extract_pdf
image_analysis
```

---

# 19. Tool Registry

All tools are centrally registered.

Each tool exposes:

```typescript
interface Tool {
    name: string
    description: string
    inputSchema: object
    outputSchema: object
    permissions: PermissionPolicy

    execute(input, context)
}
```

Registry responsibilities:

```text
discovery
validation
permission enforcement
execution
logging
metrics
```

---

# 20. Permission Framework

Every tool invocation passes through the Permission Engine.

Permission modes:

```text
allow
ask
deny
```

Examples:

```json
{
  "shell": "ask",
  "git_commit": "ask",
  "browser_use": "allow",
  "delete_file": "ask",
  "internet": "ask"
}
```

Users may override permissions globally, per-project, or per-agent.

---

# 21. Memory Architecture

Iron Jarvis uses layered memory.

## Layer 1 — Session Memory

Short-term working memory.

Stores:

```text
conversation history
tool outputs
intermediate reasoning summaries
```

---

## Layer 2 — Project Memory

Repository-scoped memory.

Location:

```text
.ironjarvis/memory/
```

Examples:

```text
MEMORY.md
architecture.md
decisions.md
coding-standards.md
known-issues.md
workflows.md
```

---

## Layer 3 — User Memory

Persistent user preferences.

Examples:

```text
preferred models
coding style
review preferences
risk tolerance
workflow preferences
```

---

## Layer 4 — Organizational Memory

Optional shared memory for teams.

---

# 22. Retrieval System

Primary retrieval mechanisms:

```text
repository search
memory search
skill search
semantic search
vector retrieval
document retrieval
web retrieval
```

Supported storage:

```text
SQLite
Chroma
LanceDB
Postgres + pgvector
Qdrant
```

---

# 23. Skills Framework

Skills are reusable instruction bundles.

Examples:

```text
Tax Preparation
QuickBooks
Deep Research
Software Architecture
Financial Analysis
PDF Processing
Code Review
```

Structure:

```text
.ironjarvis/skills/

tax/
    SKILL.md

quickbooks/
    SKILL.md

research/
    SKILL.md
```

A skill may contain:

```text
instructions
examples
scripts
templates
documents
workflows
```

---

# 24. Workflow Engine

Workflows define repeatable processes.

Examples:

```text
Monthly bookkeeping
Tax return preparation
Software release
Research pipeline
Client onboarding
```

Workflow components:

```text
triggers
conditions
steps
agents
tools
outputs
notifications
```

---

# 25. Trigger System

Supported triggers:

```text
manual
cron
webhook
file change
email
calendar event
API request
```

Example:

```toml
[[triggers]]
name = "monthly-bookkeeping"
schedule = "0 8 1 * *"
workflow = "bookkeeping-workflow"
```

---

# 26. Artifact System

Artifacts are outputs generated by agents.

Examples:

```text
reports
spreadsheets
documents
presentations
code patches
research summaries
images
videos
```

Artifacts are versioned.

Location:

```text
.ironjarvis/artifacts/
```

---

# 27. Git Integration

Every project is git-native.

Execution model:

```text
Create Session
Create Branch
Perform Work
Run Validation
Generate Diff
User Review
Approve
Merge
```

Branch format:

```text
ironjarvis/session-<timestamp>-<slug>
```

Agents are prohibited from merging automatically.

---

# 28. Review System

The Review Engine displays:

```text
modified files
inline diffs
test results
risk assessments
tool history
summary
```

User actions:

```text
approve
reject
continue session
create pull request
export patch
```

---

# 29. Evaluation Engine

Iron Jarvis continuously evaluates agent performance.

Metrics:

```text
task completion
tool success rate
hallucination rate
code quality
review acceptance rate
execution cost
latency
```

Evaluations are stored for improvement and replay.

---

# 30. Observability System

Observability components:

```text
structured logs
metrics
event stream
execution traces
provider traces
tool traces
sandbox traces
```

Purpose:

```text
debugging
performance tuning
evaluation
auditing
```

---

# 31. Event Bus

The Event Bus coordinates the platform.

Events include:

```text
session.created
agent.started
tool.executed
artifact.generated
memory.updated
workflow.completed
review.requested
provider.failed
```

Recommended implementations:

```text
Node EventEmitter
Redis Streams
NATS
Kafka (future)
```

---

# 32. Future Expansion Areas

Potential future capabilities:

```text
voice interface
mobile companion
distributed agent clusters
GPU scheduling
team collaboration
marketplace
robotics integration
physical device control
AR/VR interfaces
```

---

# 33. Final Product Definition

Iron Jarvis is a local-first AI operating system that orchestrates agents, models, tools, memory, and workflows to autonomously perform meaningful work while preserving complete user ownership, transparency, and control.

Core architecture:

```text
Dashboard
+
Orchestrator
+
Agent Runtime
+
Model Router
+
Provider Manager
+
Sandbox Manager
+
Memory System
+
Tool Registry
+
Workflow Engine
+
Review Engine
+
Git Integration
+
Evaluation Engine
+
Observability
```

Everything else is built on top of this foundation.
