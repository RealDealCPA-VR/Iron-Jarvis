# IJ-BENCH-HIDDEN-CHECK frontier-legacy-callers
"""Hidden check: the list of places in src/ that call audit.log.emit_legacy."""
import hashlib
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
#: The project's files (made by the setup step) must be unchanged.
SCOPE = ('README.md', 'src', 'vendor', 'docs', 'config')
#: task.md: "write only answer.json (delete any scratch files you make)". Besides
#: the project and the answer, only this check's own file(s), caches and dot-folders
#: a tool may keep (.git, .claude, ...) may be here.
ANSWER = "answer.json"
TREE = "bd7a012500c0b5be4b86ec110dc211e63e05a42de51ba55a4276bfe73ef533ad"
EXPECTED = [
    "src/accounts/item_09.py:33",
    "src/accounts/record_28.py:44",
    "src/accounts/report_26.py:21",
    "src/admin/draft_18.py:43",
    "src/analytics/item_32.py:33",
    "src/analytics/item_32.py:43",
    "src/analytics/note_04.py:32",
    "src/audit/log.py:31",
    "src/billing/batch_24.py:43",
    "src/billing/batch_24.py:49",
    "src/billing/item_16.py:21",
    "src/billing/token_04.py:32",
    "src/catalog/entry_09.py:44",
    "src/catalog/event_22.py:21",
    "src/catalog/event_22.py:28",
    "src/checkout/account_16.py:43",
    "src/checkout/entry_34.py:32",
    "src/checkout/line_27.py:43",
    "src/checkout/line_27.py:50",
    "src/cms/line_29.py:43",
    "src/cms/order_17.py:33",
    "src/coupons/rate_24.py:32",
    "src/crm/invoice_25.py:43",
    "src/crm/label_13.py:44",
    "src/exports/quota_17.py:32",
    "src/gateway/line_37.py:21",
    "src/imports/entry_23.py:43",
    "src/invoicing/draft_13.py:33",
    "src/invoicing/draft_13.py:40",
    "src/invoicing/entry_27.py:22",
    "src/ledger/label_31.py:32",
    "src/ledger/label_31.py:42",
    "src/loyalty/rule_21.py:22",
    "src/loyalty/rule_21.py:32",
    "src/mailer/account_00.py:43",
    "src/mailer/account_00.py:49",
    "src/mailer/draft_07.py:22",
    "src/mailer/note_38.py:21",
    "src/orders/order_23.py:21",
    "src/payments/token_09.py:43",
    "src/promotions/batch_05.py:43",
    "src/promotions/entry_19.py:32",
    "src/promotions/entry_19.py:39",
    "src/promotions/slot_04.py:21",
    "src/reports/quota_26.py:22",
    "src/scheduler/batch_15.py:32",
    "src/search/note_14.py:33",
    "src/search/record_20.py:43",
    "src/search/record_20.py:50",
    "src/shipping/draft_32.py:44",
    "src/shipping/draft_32.py:51",
    "src/support/rate_12.py:44",
    "src/tax/rate_33.py:43",
    "src/tax/rate_33.py:53",
    "src/users/item_22.py:43",
    "src/users/order_08.py:22",
    "src/users/order_08.py:29",
    "src/users/slot_05.py:22",
    "src/warehouse/event_24.py:21",
    "src/webhooks/draft_26.py:33",
    "src/webhooks/event_08.py:32",
    "src/webhooks/note_28.py:32",
    "src/wishlist/invoice_09.py:22",
]


def _digest(root):
    h = hashlib.sha256()
    for top in SCOPE:
        base = os.path.join(root, top)
        if os.path.isfile(base):
            paths = [base]
        else:
            paths = []
            for folder, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                paths += [os.path.join(folder, f) for f in files]
            paths.sort(key=lambda p: os.path.relpath(p, root).replace(os.sep, "/"))
        for p in paths:
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            with open(p, "rb") as fh:
                data = fh.read().replace(b"\r\n", b"\n")
            h.update(rel.encode() + b"\0")
            h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def _norm(entry):
    text = str(entry).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


class HiddenCallers(unittest.TestCase):
    def setUp(self):
        path = os.path.join(HERE, "answer.json")
        self.assertTrue(os.path.isfile(path), "answer.json was not written")
        # json.loads on the BYTES reads UTF-8 (with or without a BOM) and the
        # UTF-16 that Windows PowerShell 5.1's Out-File writes (v1.400.2).
        with open(path, "rb") as fh:
            self.answer = json.loads(fh.read())
        self.assertIsInstance(self.answer, dict, "answer.json must hold a JSON object")

    def test_the_project_was_not_changed(self):
        self.assertEqual(_digest(HERE), TREE, "files of the project were changed, added or removed")

    def test_every_call_and_nothing_else(self):
        calls = self.answer.get("calls")
        self.assertIsInstance(calls, list, "calls must be a list")
        got = [_norm(c) for c in calls]
        self.assertEqual(len(got), len(set(got)), "a place is listed twice")
        missing = sorted(set(EXPECTED) - set(got))
        extra = sorted(set(got) - set(EXPECTED))
        self.assertEqual(
            (missing, extra), ([], []),
            f"{len(missing)} call(s) missing (e.g. {missing[:3]}), {len(extra)} listed that are not "
            f"calls of audit.log.emit_legacy (e.g. {extra[:3]})",
        )

    def test_the_count(self):
        self.assertEqual(self.answer.get("count"), len(EXPECTED))

    def test_the_list_is_sorted_by_path_then_line(self):
        # task.md: "Sort the list by path, then by line number".
        calls = self.answer.get("calls")
        self.assertIsInstance(calls, list, "calls must be a list")

        def key(entry):
            path, _, line = _norm(entry).rpartition(":")
            return (path, int(line) if line.isdigit() else -1)

        got = [_norm(c) for c in calls]
        self.assertEqual(got, sorted(got, key=key), "the list is not sorted by path, then by line number")

    def test_nothing_but_the_answer_was_left_in_the_folder(self):
        # task.md: "write only answer.json (delete any scratch files you make)".
        mine = {os.path.basename(__file__)}
        allowed = set(SCOPE) | {ANSWER} | mine | {"__pycache__"}
        left = sorted(name for name in os.listdir(HERE) if name not in allowed and not name.startswith("."))
        self.assertEqual(left, [], "files left in the folder besides answer.json")
