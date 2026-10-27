{
 "writes": [
  {
   "file": "shop/accounts/invites.py",
   "function": "finish_invite",
   "event": "invite.changed"
  },
  {
   "file": "shop/accounts/lockout.py",
   "function": "LockoutService.process_lockout",
   "event": "session.opened"
  },
  {
   "file": "shop/accounts/lockout.py",
   "function": "LockoutService.process_lockout",
   "event": "role.revoked"
  },
  {
   "file": "shop/accounts/passwords.py",
   "function": "PasswordService.process_password",
   "event": "session.changed"
  },
  {
   "file": "shop/accounts/passwords.py",
   "function": "PasswordService.process_password",
   "event": "password.granted"
  },
  {
   "file": "shop/accounts/profiles.py",
   "function": "ProfileService.process_profile",
   "event": "password.created"
  },
  {
   "file": "shop/accounts/roles.py",
   "function": "handle_role",
   "event": "role.unlocked"
  },
  {
   "file": "shop/accounts/roles.py",
   "function": "process_role",
   "event": "session.created"
  },
  {
   "file": "shop/accounts/roles.py",
   "function": "process_role",
   "event": "role.opened"
  },
  {
   "file": "shop/accounts/sessions.py",
   "function": "record_session",
   "event": "session.reset"
  },
  {
   "file": "shop/accounts/sessions.py",
   "function": "apply_session",
   "event": "failure.accounts_locked"
  },
  {
   "file": "shop/accounts/sessions.py",
   "function": "handle_session",
   "event": "role.locked"
  },
  {
   "file": "shop/accounts/users.py",
   "function": "UserService.sync_user",
   "event": "invite.revoked"
  },
  {
   "file": "shop/catalog/categories.py",
   "function": "CategorieService.record_categorie",
   "event": "image.renamed"
  },
  {
   "file": "shop/catalog/categories.py",
   "function": "CategorieService.handle_categorie",
   "event": "listing.renamed"
  },
  {
   "file": "shop/catalog/categories.py",
   "function": "CategorieService.handle_categorie",
   "event": "image.created"
  },
  {
   "file": "shop/catalog/categories.py",
   "function": "CategorieService.start_categorie",
   "event": "product.renamed"
  },
  {
   "file": "shop/catalog/imports.py",
   "function": "ImportService.record_import",
   "event": "price.renamed"
  },
  {
   "file": "shop/catalog/imports.py",
   "function": "ImportService.start_import",
   "event": "price.hidden"
  },
  {
   "file": "shop/catalog/media.py",
   "function": "process_media",
   "event": "image.repriced"
  },
  {
   "file": "shop/catalog/media.py",
   "function": "handle_media",
   "event": "product.hidden"
  },
  {
   "file": "shop/catalog/pricing.py",
   "function": "handle_pricing",
   "event": "failure.catalog_published"
  },
  {
   "file": "shop/catalog/products.py",
   "function": "apply_product",
   "event": "price.imported"
  },
  {
   "file": "shop/catalog/products.py",
   "function": "apply_product",
   "event": "product.repriced"
  },
  {
   "file": "shop/catalog/search.py",
   "function": "check_search",
   "event": "product.archived"
  },
  {
   "file": "shop/inventory/alerts.py",
   "function": "apply_alert",
   "event": "transfer.written_off"
  },
  {
   "file": "shop/inventory/alerts.py",
   "function": "process_alert",
   "event": "item.released"
  },
  {
   "file": "shop/inventory/bins.py",
   "function": "BinService.start_bin",
   "event": "failure.inventory_approved"
  },
  {
   "file": "shop/inventory/bins.py",
   "function": "BinService.start_bin",
   "event": "item.written_off"
  },
  {
   "file": "shop/inventory/bins.py",
   "function": "BinService.record_bin",
   "event": "bin.adjusted"
  },
  {
   "file": "shop/inventory/counts.py",
   "function": "CountService.process_count",
   "event": "count.counted"
  },
  {
   "file": "shop/inventory/counts.py",
   "function": "CountService.start_count",
   "event": "count.moved"
  },
  {
   "file": "shop/inventory/counts.py",
   "function": "CountService.start_count",
   "event": "transfer.reserved"
  },
  {
   "file": "shop/inventory/stock.py",
   "function": "handle_stock",
   "event": "bin.written_off"
  },
  {
   "file": "shop/inventory/stock.py",
   "function": "start_stock",
   "event": "count.released"
  },
  {
   "file": "shop/inventory/transfers.py",
   "function": "start_transfer",
   "event": "item.adjusted"
  },
  {
   "file": "shop/inventory/transfers.py",
   "function": "update_transfer",
   "event": "item.moved"
  },
  {
   "file": "shop/inventory/writeoffs.py",
   "function": "finish_writeoff",
   "event": "failure.inventory_reserved"
  },
  {
   "file": "shop/inventory/writeoffs.py",
   "function": "start_writeoff",
   "event": "stock.released"
  },
  {
   "file": "shop/orders/carts.py",
   "function": "CartService.record_cart",
   "event": "return.cancelled"
  },
  {
   "file": "shop/orders/carts.py",
   "function": "CartService.record_cart",
   "event": "invoice.split"
  },
  {
   "file": "shop/orders/carts.py",
   "function": "CartService.sync_cart",
   "event": "failure.orders_confirmed"
  },
  {
   "file": "shop/orders/notes.py",
   "function": "NoteService.process_note",
   "event": "cart.voided"
  },
  {
   "file": "shop/orders/notes.py",
   "function": "NoteService.process_note",
   "event": "invoice.cancelled"
  },
  {
   "file": "shop/orders/notes.py",
   "function": "NoteService.close_note",
   "event": "invoice.merged"
  },
  {
   "file": "shop/orders/returns.py",
   "function": "start_return",
   "event": "checkout.confirmed"
  },
  {
   "file": "shop/orders/service.py",
   "function": "ServiceService.start_service",
   "event": "cart.placed"
  },
  {
   "file": "shop/orders/service.py",
   "function": "ServiceService.record_service",
   "event": "order.merged"
  },
  {
   "file": "shop/orders/splits.py",
   "function": "SplitService.sync_split",
   "event": "checkout.merged"
  },
  {
   "file": "shop/payments/capture.py",
   "function": "check_capture",
   "event": "card.opened"
  },
  {
   "file": "shop/payments/capture.py",
   "function": "start_capture",
   "event": "dispute.declined"
  },
  {
   "file": "shop/payments/capture.py",
   "function": "close_capture",
   "event": "dispute.sent"
  },
  {
   "file": "shop/payments/capture.py",
   "function": "close_capture",
   "event": "failure.payments_opened"
  },
  {
   "file": "shop/payments/cards.py",
   "function": "CardService.apply_card",
   "event": "refund.retried"
  },
  {
   "file": "shop/payments/cards.py",
   "function": "CardService.apply_card",
   "event": "refund.sent"
  },
  {
   "file": "shop/payments/disputes.py",
   "function": "DisputeService.handle_dispute",
   "event": "payout.removed"
  },
  {
   "file": "shop/payments/disputes.py",
   "function": "DisputeService.check_dispute",
   "event": "payment.retried"
  },
  {
   "file": "shop/payments/disputes.py",
   "function": "DisputeService.start_dispute",
   "event": "card.sent"
  },
  {
   "file": "shop/payments/disputes.py",
   "function": "DisputeService.start_dispute",
   "event": "card.refunded"
  },
  {
   "file": "shop/payments/ledger.py",
   "function": "update_ledger",
   "event": "dispute.refunded"
  },
  {
   "file": "shop/payments/ledger.py",
   "function": "handle_ledger",
   "event": "refund.declined"
  },
  {
   "file": "shop/payments/payouts.py",
   "function": "PayoutService.process_payout",
   "event": "card.retried"
  },
  {
   "file": "shop/payments/payouts.py",
   "function": "PayoutService.update_payout",
   "event": "payment.sent"
  },
  {
   "file": "shop/payments/refunds.py",
   "function": "process_refund",
   "event": "dispute.added"
  },
  {
   "file": "shop/payments/refunds.py",
   "function": "check_refund",
   "event": "payout.refunded"
  },
  {
   "file": "shop/payments/refunds.py",
   "function": "update_refund",
   "event": "card.added"
  },
  {
   "file": "shop/payments/refunds.py",
   "function": "update_refund",
   "event": "payout.sent"
  },
  {
   "file": "shop/payments/retries.py",
   "function": "RetrieService.update_retrie",
   "event": "payment.added"
  },
  {
   "file": "shop/payments/retries.py",
   "function": "RetrieService.check_retrie",
   "event": "payment.refunded"
  },
  {
   "file": "shop/payments/retries.py",
   "function": "RetrieService.close_retrie",
   "event": "payout.declined"
  },
  {
   "file": "shop/shipping/carriers.py",
   "function": "close_carrier",
   "event": "parcel.delivered"
  },
  {
   "file": "shop/shipping/labels.py",
   "function": "LabelService.apply_label",
   "event": "shipment.voided"
  },
  {
   "file": "shop/shipping/pickups.py",
   "function": "record_pickup",
   "event": "label.delivered"
  },
  {
   "file": "shop/shipping/pickups.py",
   "function": "close_pickup",
   "event": "carrier.switched"
  },
  {
   "file": "shop/shipping/pickups.py",
   "function": "close_pickup",
   "event": "shipment.switched"
  },
  {
   "file": "shop/shipping/rates.py",
   "function": "start_rate",
   "event": "carrier.voided"
  },
  {
   "file": "shop/shipping/rates.py",
   "function": "update_rate",
   "event": "carrier.booked"
  },
  {
   "file": "shop/shipping/rates.py",
   "function": "close_rate",
   "event": "label.printed"
  },
  {
   "file": "shop/shipping/rates.py",
   "function": "close_rate",
   "event": "label.dispatched"
  },
  {
   "file": "shop/shipping/returns.py",
   "function": "ReturnService.process_return",
   "event": "pickup.switched"
  },
  {
   "file": "shop/shipping/returns.py",
   "function": "ReturnService.process_return",
   "event": "parcel.booked"
  },
  {
   "file": "shop/shipping/returns.py",
   "function": "ReturnService.record_return",
   "event": "failure.shipping_printed"
  },
  {
   "file": "shop/shipping/shipments.py",
   "function": "ShipmentService.finish_shipment",
   "event": "shipment.booked"
  },
  {
   "file": "shop/shipping/tracking.py",
   "function": "TrackingService.sync_tracking",
   "event": "shipment.returned"
  },
  {
   "file": "shop/shipping/tracking.py",
   "function": "TrackingService.sync_tracking",
   "event": "parcel.switched"
  },
  {
   "file": "shop/shipping/tracking.py",
   "function": "TrackingService.record_tracking",
   "event": "carrier.dispatched"
  },
  {
   "file": "shop/shipping/tracking.py",
   "function": "TrackingService.record_tracking",
   "event": "carrier.delivered"
  },
  {
   "file": "shop/support/callbacks.py",
   "function": "finish_callback",
   "event": "note.opened"
  },
  {
   "file": "shop/support/escalation.py",
   "function": "EscalationService.record_escalation",
   "event": "failure.support_opened"
  },
  {
   "file": "shop/support/escalation.py",
   "function": "EscalationService.finish_escalation",
   "event": "survey.sent"
  },
  {
   "file": "shop/support/macros.py",
   "function": "MacroService.record_macro",
   "event": "macro.opened"
  },
  {
   "file": "shop/support/macros.py",
   "function": "MacroService.start_macro",
   "event": "ticket.reassigned"
  },
  {
   "file": "shop/support/notes.py",
   "function": "sync_note",
   "event": "macro.sent"
  },
  {
   "file": "shop/support/notes.py",
   "function": "start_note",
   "event": "note.sent"
  },
  {
   "file": "shop/support/sla.py",
   "function": "process_sla",
   "event": "survey.booked"
  },
  {
   "file": "shop/support/surveys.py",
   "function": "SurveyService.apply_survey",
   "event": "callback.booked"
  },
  {
   "file": "shop/support/tickets.py",
   "function": "record_ticket",
   "event": "macro.reassigned"
  },
  {
   "file": "shop/support/tickets.py",
   "function": "record_ticket",
   "event": "callback.closed"
  },
  {
   "file": "shop/support/tickets.py",
   "function": "sync_ticket",
   "event": "ticket.escalated"
  },
  {
   "file": "shop/support/tickets.py",
   "function": "sync_ticket",
   "event": "callback.reassigned"
  },
  {
   "file": "shop/reports/builders.py",
   "function": "BuilderService.handle_builder",
   "event": "export.exported"
  },
  {
   "file": "shop/reports/builders.py",
   "function": "BuilderService.handle_builder",
   "event": "digest.scheduled"
  },
  {
   "file": "shop/reports/dashboards.py",
   "function": "DashboardService.check_dashboard",
   "event": "digest.generated"
  },
  {
   "file": "shop/reports/dashboards.py",
   "function": "DashboardService.sync_dashboard",
   "event": "schedule.sent"
  },
  {
   "file": "shop/reports/dashboards.py",
   "function": "DashboardService.sync_dashboard",
   "event": "schedule.exported"
  },
  {
   "file": "shop/reports/digest.py",
   "function": "check_digest",
   "event": "report.scheduled"
  },
  {
   "file": "shop/reports/exports.py",
   "function": "ExportService.handle_export",
   "event": "digest.exported"
  },
  {
   "file": "shop/reports/exports.py",
   "function": "ExportService.handle_export",
   "event": "dashboard.shared"
  },
  {
   "file": "shop/reports/exports.py",
   "function": "ExportService.process_export",
   "event": "schedule.generated"
  },
  {
   "file": "shop/reports/exports.py",
   "function": "ExportService.sync_export",
   "event": "dashboard.deleted"
  },
  {
   "file": "shop/reports/retention.py",
   "function": "RetentionService.apply_retention",
   "event": "digest.shared"
  },
  {
   "file": "shop/reports/retention.py",
   "function": "RetentionService.apply_retention",
   "event": "dashboard.generated"
  },
  {
   "file": "shop/reports/retention.py",
   "function": "RetentionService.handle_retention",
   "event": "export.deleted"
  },
  {
   "file": "shop/reports/retention.py",
   "function": "RetentionService.start_retention",
   "event": "schedule.deleted"
  },
  {
   "file": "shop/reports/schedules.py",
   "function": "ScheduleService.handle_schedule",
   "event": "report.generated"
  },
  {
   "file": "shop/reports/schedules.py",
   "function": "ScheduleService.finish_schedule",
   "event": "dashboard.scheduled"
  },
  {
   "file": "shop/reports/sharing.py",
   "function": "process_sharing",
   "event": "export.sent"
  },
  {
   "file": "shop/reports/sharing.py",
   "function": "process_sharing",
   "event": "failure.reports_scheduled"
  },
  {
   "file": "shop/reports/sharing.py",
   "function": "apply_sharing",
   "event": "dashboard.exported"
  },
  {
   "file": "shop/audit/log.py",
   "function": "rotate",
   "event": "audit.rotated"
  }
 ]
}