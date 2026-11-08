"""Wave D items 32 and 33, the schema half.

* The first Models row reads "Default AI", never the engine room's "Default
  provider"; the old words stay searchable (alias) and still arm chat's
  settings tools.
* ``account_groups`` stays an ``advanced`` dict row (the dashboard folds it,
  and SettingsHome hides it behind the Several accounts card's Group picker).
"""

from __future__ import annotations

from iron_jarvis.settings import schema
from iron_jarvis.settings.trigger import settings_tools_for


def test_default_provider_reads_plain_words_and_keeps_its_old_name_searchable():
    d = schema.get("default_provider")
    assert d.label == "Default AI"
    assert "provider" not in d.label.lower()
    assert "default provider" in d.aliases


def test_the_old_words_still_arm_the_settings_tools():
    assert "config_set" in settings_tools_for("change my default provider to OpenAI", auto_tools=True)
    assert "config_set" in settings_tools_for("change my default AI to OpenAI", auto_tools=True)


def test_account_groups_is_an_advanced_json_row():
    d = schema.get("account_groups")
    assert d.advanced and d.type == "dict"
