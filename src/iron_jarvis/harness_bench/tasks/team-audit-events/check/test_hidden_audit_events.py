# IJ-BENCH-HIDDEN-CHECK team-audit-events
"""Hidden check: answer.json lists exactly the audit writes of shop/ (recorded
by the generator as it wrote each one), and the project is unchanged."""
import hashlib
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPE = ('README.md', 'docs', 'shop')
TREE = "54329256e772ad047d45063bf262f291a097b625d0661f7d25baef19a95b86d9"
EXPECTED = [
    ("shop/accounts/invites.py", "finish_invite", "invite.changed"),
    ("shop/accounts/lockout.py", "LockoutService.process_lockout", "role.revoked"),
    ("shop/accounts/lockout.py", "LockoutService.process_lockout", "session.opened"),
    ("shop/accounts/passwords.py", "PasswordService.process_password", "password.granted"),
    ("shop/accounts/passwords.py", "PasswordService.process_password", "session.changed"),
    ("shop/accounts/profiles.py", "ProfileService.process_profile", "password.created"),
    ("shop/accounts/roles.py", "handle_role", "role.unlocked"),
    ("shop/accounts/roles.py", "process_role", "role.opened"),
    ("shop/accounts/roles.py", "process_role", "session.created"),
    ("shop/accounts/sessions.py", "apply_session", "failure.accounts_locked"),
    ("shop/accounts/sessions.py", "handle_session", "role.locked"),
    ("shop/accounts/sessions.py", "record_session", "session.reset"),
    ("shop/accounts/users.py", "UserService.sync_user", "invite.revoked"),
    ("shop/audit/log.py", "rotate", "audit.rotated"),
    ("shop/catalog/categories.py", "CategorieService.handle_categorie", "image.created"),
    ("shop/catalog/categories.py", "CategorieService.handle_categorie", "listing.renamed"),
    ("shop/catalog/categories.py", "CategorieService.record_categorie", "image.renamed"),
    ("shop/catalog/categories.py", "CategorieService.start_categorie", "product.renamed"),
    ("shop/catalog/imports.py", "ImportService.record_import", "price.renamed"),
    ("shop/catalog/imports.py", "ImportService.start_import", "price.hidden"),
    ("shop/catalog/media.py", "handle_media", "product.hidden"),
    ("shop/catalog/media.py", "process_media", "image.repriced"),
    ("shop/catalog/pricing.py", "handle_pricing", "failure.catalog_published"),
    ("shop/catalog/products.py", "apply_product", "price.imported"),
    ("shop/catalog/products.py", "apply_product", "product.repriced"),
    ("shop/catalog/search.py", "check_search", "product.archived"),
    ("shop/inventory/alerts.py", "apply_alert", "transfer.written_off"),
    ("shop/inventory/alerts.py", "process_alert", "item.released"),
    ("shop/inventory/bins.py", "BinService.record_bin", "bin.adjusted"),
    ("shop/inventory/bins.py", "BinService.start_bin", "failure.inventory_approved"),
    ("shop/inventory/bins.py", "BinService.start_bin", "item.written_off"),
    ("shop/inventory/counts.py", "CountService.process_count", "count.counted"),
    ("shop/inventory/counts.py", "CountService.start_count", "count.moved"),
    ("shop/inventory/counts.py", "CountService.start_count", "transfer.reserved"),
    ("shop/inventory/stock.py", "handle_stock", "bin.written_off"),
    ("shop/inventory/stock.py", "start_stock", "count.released"),
    ("shop/inventory/transfers.py", "start_transfer", "item.adjusted"),
    ("shop/inventory/transfers.py", "update_transfer", "item.moved"),
    ("shop/inventory/writeoffs.py", "finish_writeoff", "failure.inventory_reserved"),
    ("shop/inventory/writeoffs.py", "start_writeoff", "stock.released"),
    ("shop/orders/carts.py", "CartService.record_cart", "invoice.split"),
    ("shop/orders/carts.py", "CartService.record_cart", "return.cancelled"),
    ("shop/orders/carts.py", "CartService.sync_cart", "failure.orders_confirmed"),
    ("shop/orders/notes.py", "NoteService.close_note", "invoice.merged"),
    ("shop/orders/notes.py", "NoteService.process_note", "cart.voided"),
    ("shop/orders/notes.py", "NoteService.process_note", "invoice.cancelled"),
    ("shop/orders/returns.py", "start_return", "checkout.confirmed"),
    ("shop/orders/service.py", "ServiceService.record_service", "order.merged"),
    ("shop/orders/service.py", "ServiceService.start_service", "cart.placed"),
    ("shop/orders/splits.py", "SplitService.sync_split", "checkout.merged"),
    ("shop/payments/capture.py", "check_capture", "card.opened"),
    ("shop/payments/capture.py", "close_capture", "dispute.sent"),
    ("shop/payments/capture.py", "close_capture", "failure.payments_opened"),
    ("shop/payments/capture.py", "start_capture", "dispute.declined"),
    ("shop/payments/cards.py", "CardService.apply_card", "refund.retried"),
    ("shop/payments/cards.py", "CardService.apply_card", "refund.sent"),
    ("shop/payments/disputes.py", "DisputeService.check_dispute", "payment.retried"),
    ("shop/payments/disputes.py", "DisputeService.handle_dispute", "payout.removed"),
    ("shop/payments/disputes.py", "DisputeService.start_dispute", "card.refunded"),
    ("shop/payments/disputes.py", "DisputeService.start_dispute", "card.sent"),
    ("shop/payments/ledger.py", "handle_ledger", "refund.declined"),
    ("shop/payments/ledger.py", "update_ledger", "dispute.refunded"),
    ("shop/payments/payouts.py", "PayoutService.process_payout", "card.retried"),
    ("shop/payments/payouts.py", "PayoutService.update_payout", "payment.sent"),
    ("shop/payments/refunds.py", "check_refund", "payout.refunded"),
    ("shop/payments/refunds.py", "process_refund", "dispute.added"),
    ("shop/payments/refunds.py", "update_refund", "card.added"),
    ("shop/payments/refunds.py", "update_refund", "payout.sent"),
    ("shop/payments/retries.py", "RetrieService.check_retrie", "payment.refunded"),
    ("shop/payments/retries.py", "RetrieService.close_retrie", "payout.declined"),
    ("shop/payments/retries.py", "RetrieService.update_retrie", "payment.added"),
    ("shop/reports/builders.py", "BuilderService.handle_builder", "digest.scheduled"),
    ("shop/reports/builders.py", "BuilderService.handle_builder", "export.exported"),
    ("shop/reports/dashboards.py", "DashboardService.check_dashboard", "digest.generated"),
    ("shop/reports/dashboards.py", "DashboardService.sync_dashboard", "schedule.exported"),
    ("shop/reports/dashboards.py", "DashboardService.sync_dashboard", "schedule.sent"),
    ("shop/reports/digest.py", "check_digest", "report.scheduled"),
    ("shop/reports/exports.py", "ExportService.handle_export", "dashboard.shared"),
    ("shop/reports/exports.py", "ExportService.handle_export", "digest.exported"),
    ("shop/reports/exports.py", "ExportService.process_export", "schedule.generated"),
    ("shop/reports/exports.py", "ExportService.sync_export", "dashboard.deleted"),
    ("shop/reports/retention.py", "RetentionService.apply_retention", "dashboard.generated"),
    ("shop/reports/retention.py", "RetentionService.apply_retention", "digest.shared"),
    ("shop/reports/retention.py", "RetentionService.handle_retention", "export.deleted"),
    ("shop/reports/retention.py", "RetentionService.start_retention", "schedule.deleted"),
    ("shop/reports/schedules.py", "ScheduleService.finish_schedule", "dashboard.scheduled"),
    ("shop/reports/schedules.py", "ScheduleService.handle_schedule", "report.generated"),
    ("shop/reports/sharing.py", "apply_sharing", "dashboard.exported"),
    ("shop/reports/sharing.py", "process_sharing", "export.sent"),
    ("shop/reports/sharing.py", "process_sharing", "failure.reports_scheduled"),
    ("shop/shipping/carriers.py", "close_carrier", "parcel.delivered"),
    ("shop/shipping/labels.py", "LabelService.apply_label", "shipment.voided"),
    ("shop/shipping/pickups.py", "close_pickup", "carrier.switched"),
    ("shop/shipping/pickups.py", "close_pickup", "shipment.switched"),
    ("shop/shipping/pickups.py", "record_pickup", "label.delivered"),
    ("shop/shipping/rates.py", "close_rate", "label.dispatched"),
    ("shop/shipping/rates.py", "close_rate", "label.printed"),
    ("shop/shipping/rates.py", "start_rate", "carrier.voided"),
    ("shop/shipping/rates.py", "update_rate", "carrier.booked"),
    ("shop/shipping/returns.py", "ReturnService.process_return", "parcel.booked"),
    ("shop/shipping/returns.py", "ReturnService.process_return", "pickup.switched"),
    ("shop/shipping/returns.py", "ReturnService.record_return", "failure.shipping_printed"),
    ("shop/shipping/shipments.py", "ShipmentService.finish_shipment", "shipment.booked"),
    ("shop/shipping/tracking.py", "TrackingService.record_tracking", "carrier.delivered"),
    ("shop/shipping/tracking.py", "TrackingService.record_tracking", "carrier.dispatched"),
    ("shop/shipping/tracking.py", "TrackingService.sync_tracking", "parcel.switched"),
    ("shop/shipping/tracking.py", "TrackingService.sync_tracking", "shipment.returned"),
    ("shop/support/callbacks.py", "finish_callback", "note.opened"),
    ("shop/support/escalation.py", "EscalationService.finish_escalation", "survey.sent"),
    ("shop/support/escalation.py", "EscalationService.record_escalation", "failure.support_opened"),
    ("shop/support/macros.py", "MacroService.record_macro", "macro.opened"),
    ("shop/support/macros.py", "MacroService.start_macro", "ticket.reassigned"),
    ("shop/support/notes.py", "start_note", "note.sent"),
    ("shop/support/notes.py", "sync_note", "macro.sent"),
    ("shop/support/sla.py", "process_sla", "survey.booked"),
    ("shop/support/surveys.py", "SurveyService.apply_survey", "callback.booked"),
    ("shop/support/tickets.py", "record_ticket", "callback.closed"),
    ("shop/support/tickets.py", "record_ticket", "macro.reassigned"),
    ("shop/support/tickets.py", "sync_ticket", "callback.reassigned"),
    ("shop/support/tickets.py", "sync_ticket", "ticket.escalated"),
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
                data = fh.read().replace(bytes([13, 10]), bytes([10]))
            h.update(rel.encode() + b"\0")
            h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def _file(value):
    text = str(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lower()


def _entry(raw):
    if not isinstance(raw, dict):
        return None
    try:
        return (_file(raw["file"]), str(raw["function"]).strip(), str(raw["event"]).strip().lower())
    except KeyError:
        return None


class HiddenAuditEvents(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        path = os.path.join(HERE, "answer.json")
        self.assertTrue(os.path.isfile(path), "answer.json was not written")
        # json.loads on the BYTES reads UTF-8 (with or without a BOM) and the
        # UTF-16 that Windows PowerShell 5.1's Out-File writes.
        with open(path, "rb") as fh:
            self.answer = json.loads(fh.read())
        self.assertIsInstance(self.answer, dict, "answer.json must hold a JSON object")

    def test_a_the_project_was_not_changed(self):
        self.assertEqual(_digest(HERE), TREE, "files of the project were changed, added or removed")

    def test_b_every_write_and_nothing_else(self):
        writes = self.answer.get("writes")
        self.assertIsInstance(writes, list, "writes must be a list")
        got = [_entry(w) for w in writes]
        self.assertNotIn(None, got, "every entry needs file, function and event")
        expected = set(EXPECTED)
        missing = sorted(expected - set(got))
        extra = sorted(set(got) - expected)
        self.assertEqual(
            (missing, extra), ([], []),
            f"{len(missing)} write(s) missing (e.g. {missing[:4]}), "
            f"{len(extra)} listed that the code does not write (e.g. {extra[:4]})",
        )


if __name__ == "__main__":
    unittest.main()
