"""Integrations framework — register, configure, enable, and test bindings to
external services, each bound to *named* secrets resolved at runtime.

Generic by design: sibling feature modules (comm / ltm / webhooks) register
their own specs into the shared :class:`IntegrationRegistry`; this package never
imports them.
"""

from __future__ import annotations

from .base import Integration, IntegrationSpec, SecretResolver
from .builtin import (
    MOCK_SPEC,
    REST_SPEC,
    MockIntegration,
    RestApiIntegration,
    register_builtins,
)
from .models import IntegrationRecord
from .registry import Factory, IntegrationRegistry
from .tools import (
    IntegrationListTool,
    IntegrationTestTool,
    integration_tools,
)

__all__ = [
    "Integration",
    "IntegrationSpec",
    "SecretResolver",
    "IntegrationRecord",
    "IntegrationRegistry",
    "Factory",
    "MockIntegration",
    "RestApiIntegration",
    "MOCK_SPEC",
    "REST_SPEC",
    "register_builtins",
    "IntegrationListTool",
    "IntegrationTestTool",
    "integration_tools",
]
