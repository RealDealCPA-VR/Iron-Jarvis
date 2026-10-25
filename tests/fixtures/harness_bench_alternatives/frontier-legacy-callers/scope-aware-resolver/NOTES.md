Correct, written another way than the reference: an ast.NodeVisitor that keeps a scope
stack, binds every import (at any level) to a qualified name, lets a def or class shadow it,
follows attribute chains, and records a call whose target is audit.log.emit_legacy or the
re-export audit.emit_legacy, at the call's first line; the list sorted by path then line.
