Reviewer's alternative (v1.400.0 review): the reference rename with a CHANGELOG entry written in the
user's words. It names the rename ("Max retries" is now "retry limit", every spelling of both), as
RENAME.md asks, but never writes the old name with an underscore in lower case. The hidden check looked
for exactly `max_retries` and failed it; any spelling of the old and the new name counts.
