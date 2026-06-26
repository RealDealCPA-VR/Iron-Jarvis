"""Computer Use — a safety-critical, OPT-IN browser-automation subsystem.

Iron Jarvis can drive a real browser, but only when explicitly enabled. The
design encodes the agent-computer-use best practices as *real code*:

* Prefer APIs over UI; prefer DOM/accessibility selectors over screenshots —
  screenshot clicking is a labelled fallback (the ``screenshot_click`` kind, only
  honoured when ``fallback=True``).
* Run in an isolated/disposable browser context
  (:class:`PlaywrightBrowser` uses ``browser.new_context()``).
* Domain + action allowlists (:class:`ComputerUsePolicy`).
* Credentials, payment, PII, and destructive actions require explicit human
  approval (:class:`ApprovalQueue`).
* Treat every webpage/email/PDF/on-screen text as UNTRUSTED; stop on suspected
  prompt injection / phishing (:mod:`.safety`).
* Verify final state PROGRAMMATICALLY via checkpoint predicates — never by asking
  a model (:class:`ComputerUseHarness`).
* Record a full trace; enforce step budgets, retry limits, and recovery.

Importing this package registers its SQLModel tables on the shared metadata so
``init_db`` creates them.
"""

from __future__ import annotations

from .approvals import ApprovalQueue
from .base import (
    Action,
    ActionResult,
    Browser,
    BudgetExceeded,
    Checkpoint,
    ComputerUseDisabled,
    ComputerUseError,
    InjectionDetected,
    Page,
    PolicyDenied,
    Selector,
    UnknownSelector,
    ApprovalRequired,
)
from .browser import FakeBrowser, PlaywrightBrowser
from .harness import ApprovalResolver, ComputerUseHarness
from .models import ApprovalRequest, ComputerUseRun
from .policy import ComputerUsePolicy, Decision
from .safety import detect_injection, wrap_untrusted
from .tools import (
    BrowseTool,
    ComputerUseStatusTool,
    CUContext,
    WebActionTool,
    WebExtractTool,
    computeruse_tools,
)
from .trace import TraceRecorder

__all__ = [
    # types
    "Action",
    "ActionResult",
    "Browser",
    "Checkpoint",
    "Page",
    "Selector",
    "Decision",
    # browsers
    "FakeBrowser",
    "PlaywrightBrowser",
    # policy / safety
    "ComputerUsePolicy",
    "detect_injection",
    "wrap_untrusted",
    # harness + infra
    "ComputerUseHarness",
    "ApprovalResolver",
    "ApprovalQueue",
    "TraceRecorder",
    # tools
    "CUContext",
    "computeruse_tools",
    "BrowseTool",
    "WebExtractTool",
    "WebActionTool",
    "ComputerUseStatusTool",
    # models
    "ComputerUseRun",
    "ApprovalRequest",
    # exceptions
    "ComputerUseError",
    "ComputerUseDisabled",
    "PolicyDenied",
    "ApprovalRequired",
    "InjectionDetected",
    "BudgetExceeded",
    "UnknownSelector",
]
