"""v1.511.0 (second review D5): the finish check never re-runs a build that
ships something.

The finish check runs "the tests" again at the end of a coding reply: the TEST
PART of the model's last test command (``harness/finish.test_part``), with no
card under auto-approve or "Don't ask" when every piece of it is a built-in
test head. A whole Maven, Gradle, tox or nox line used to count as a test head
(Maven / Gradle when any plain word was ``test``; tox / nox always), so
``mvn clean test deploy``, ``./gradlew test publish``, ``tox -e release`` or
``nox -s deploy`` — run once by the model — was run a SECOND time by the check.
Now a Maven / Gradle line is a test run only when ALL its goals / tasks are
in a small test set (``clean``, ``test``, ``check``, ``verify``, also
module-qualified) and no ``-D`` / ``-P`` property names a deploy, publish or
release; a tox / nox line only when it names no environment / session, or
only test ones (``finish.TEST_ENV_WORDS``, a Python version). Anything else is
not a test run, so it is not run again. pytest lines are unchanged.

Through the public function the check uses to pick what it re-runs.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness import finish as F


@pytest.mark.parametrize("command", [
    # Maven / Gradle: only test goals and tasks
    "mvn test",
    "mvn clean test",
    "mvn -q clean verify",
    "mvn -B -pl core -am test",
    "mvn test -Dtest=ReportTest#totals",
    "mvn -P ci test",
    "./mvnw clean test",
    "mvnw.cmd test",
    "mvn surefire:test",
    "mvn test 2>&1",
    "gradle test",
    "./gradlew test",
    "./gradlew clean check",
    "gradlew.bat :app:test",
    "./gradlew :app:test --tests com.example.ReportTest",
    "./gradlew test -x lint",
    "./gradlew test -Pci=true",
    # tox / nox: no named environment, or only test ones
    "tox",
    "tox -e py311",
    "tox -e py3.12,py311",
    "tox -epy311",
    "tox run -e py",
    "tox -e tests -- -k report",
    "tox -p auto -e py311,py312",
    "nox",
    "nox -s tests",
    "nox -s tests-3.11",
    "nox --session test",
    "nox -s tests -- -k report",
    # pytest, unchanged
    "pytest",
    "pytest tests/test_report.py -q",
    "uv run pytest -q",
    "python -m pytest -x",
])
def test_a_test_only_line_is_still_re_run(command):
    assert F.test_part(command), command
    assert F.builtin_only(F.test_part(command)), command


@pytest.mark.parametrize("command", [
    # Maven / Gradle: a goal or task that ships, installs or does anything else
    "mvn clean test deploy",
    "mvn test deploy",
    "mvn clean install",
    "mvn test install",
    "mvn test release:prepare",
    "mvn test -DperformRelease=true",
    "mvn test -Dmaven.deploy.skip=false",
    "mvn -P release test",
    "mvn -Prelease test",
    "mvn clean",
    "./gradlew test publish",
    "./gradlew test publishToMavenLocal",
    "./gradlew build",
    "./gradlew test assemble",
    "gradle test -Prelease=true",
    "./gradlew :app:test :app:uploadArchives",
    # tox / nox: a named environment or session that is not a test one
    "tox -e release",
    "tox -e py311,release",
    "tox -erelease",
    "tox --env deploy",
    "tox -m release",
    "tox exec -e py -- python deploy.py",
    "tox -e docs",
    "TOXENV=release tox",
    "nox -s deploy",
    "nox -s tests deploy",
    "nox --session=publish",
    "nox -k deploy",
    "nox -t release",
    "NOXSESSION=deploy nox",
])
def test_a_line_that_ships_or_does_more_is_not_re_run(command):
    assert F.test_part(command) == "", command


def test_the_test_part_of_a_chain_keeps_only_the_test_line():
    # The model's `mvn test && mvn deploy`: only the test line is run again.
    assert F.test_part("mvn test && mvn deploy") == "mvn test"
    assert F.test_part("mvn clean test deploy && pytest -q") == "pytest -q"


def test_the_test_environment_list_is_narrow():
    assert "release" not in F.TEST_ENV_WORDS and "deploy" not in F.TEST_ENV_WORDS
    assert {"py", "test", "tests"} <= F.TEST_ENV_WORDS
