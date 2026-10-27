{
  "writes": [
    {
      "event": "audit.rotated",
      "function": "rotate",
      "file": "./shop/audit/log.py",
      "how": "via constant"
    },
    {
      "event": "audit.rotated",
      "function": "rotate",
      "file": "./shop/audit/log.py",
      "how": "via constant",
      "note": "written twice in this function"
    },
    {
      "event": "bin.adjusted",
      "function": "BinService.record_bin",
      "file": "shop\\inventory\\bins.py",
      "how": "helper"
    },
    {
      "event": "bin.written_off",
      "function": "handle_stock",
      "file": "shop/inventory/stock.py",
      "how": "via constant"
    },
    {
      "event": "callback.booked",
      "function": "SurveyService.apply_survey",
      "file": "./shop/support/surveys.py",
      "how": "helper"
    },
    {
      "event": "callback.closed",
      "function": "record_ticket",
      "file": "shop\\support\\tickets.py",
      "how": "via constant"
    },
    {
      "event": "callback.reassigned",
      "function": "sync_ticket",
      "file": "shop/support/tickets.py",
      "how": "via constant"
    },
    {
      "event": "card.added",
      "function": "update_refund",
      "file": "./shop/payments/refunds.py",
      "how": "via constant"
    },
    {
      "event": "card.opened",
      "function": "check_capture",
      "file": "shop\\payments\\capture.py",
      "how": "via constant"
    },
    {
      "event": "card.refunded",
      "function": "DisputeService.start_dispute",
      "file": "shop/payments/disputes.py",
      "how": "direct"
    },
    {
      "event": "card.retried",
      "function": "PayoutService.process_payout",
      "file": "./shop/payments/payouts.py",
      "how": "helper"
    },
    {
      "event": "card.sent",
      "function": "DisputeService.start_dispute",
      "file": "shop\\payments\\disputes.py",
      "how": "direct"
    },
    {
      "event": "card.sent",
      "function": "DisputeService.start_dispute",
      "file": "shop\\payments\\disputes.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "carrier.booked",
      "function": "update_rate",
      "file": "shop/shipping/rates.py",
      "how": "via constant"
    },
    {
      "event": "carrier.delivered",
      "function": "TrackingService.record_tracking",
      "file": "./shop/shipping/tracking.py",
      "how": "direct"
    },
    {
      "event": "carrier.dispatched",
      "function": "TrackingService.record_tracking",
      "file": "shop\\shipping\\tracking.py",
      "how": "direct"
    },
    {
      "event": "carrier.switched",
      "function": "close_pickup",
      "file": "shop/shipping/pickups.py",
      "how": "direct"
    },
    {
      "event": "carrier.voided",
      "function": "start_rate",
      "file": "./shop/shipping/rates.py",
      "how": "helper"
    },
    {
      "event": "cart.placed",
      "function": "ServiceService.start_service",
      "file": "shop\\orders\\service.py",
      "how": "helper"
    },
    {
      "event": "cart.voided",
      "function": "NoteService.process_note",
      "file": "shop/orders/notes.py",
      "how": "direct"
    },
    {
      "event": "checkout.confirmed",
      "function": "start_return",
      "file": "./shop/orders/returns.py",
      "how": "helper"
    },
    {
      "event": "checkout.merged",
      "function": "SplitService.sync_split",
      "file": "shop\\orders\\splits.py",
      "how": "via constant"
    },
    {
      "event": "count.counted",
      "function": "CountService.process_count",
      "file": "shop/inventory/counts.py",
      "how": "direct"
    },
    {
      "event": "count.counted",
      "function": "CountService.process_count",
      "file": "shop/inventory/counts.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "count.moved",
      "function": "CountService.start_count",
      "file": "./shop/inventory/counts.py",
      "how": "via constant"
    },
    {
      "event": "count.released",
      "function": "start_stock",
      "file": "shop\\inventory\\stock.py",
      "how": "direct"
    },
    {
      "event": "dashboard.deleted",
      "function": "ExportService.sync_export",
      "file": "shop/reports/exports.py",
      "how": "direct"
    },
    {
      "event": "dashboard.exported",
      "function": "apply_sharing",
      "file": "./shop/reports/sharing.py",
      "how": "via constant"
    },
    {
      "event": "dashboard.generated",
      "function": "RetentionService.apply_retention",
      "file": "shop\\reports\\retention.py",
      "how": "direct"
    },
    {
      "event": "dashboard.scheduled",
      "function": "ScheduleService.finish_schedule",
      "file": "shop/reports/schedules.py",
      "how": "helper"
    },
    {
      "event": "dashboard.shared",
      "function": "ExportService.handle_export",
      "file": "./shop/reports/exports.py",
      "how": "helper"
    },
    {
      "event": "digest.exported",
      "function": "ExportService.handle_export",
      "file": "shop\\reports\\exports.py",
      "how": "direct"
    },
    {
      "event": "digest.generated",
      "function": "DashboardService.check_dashboard",
      "file": "shop/reports/dashboards.py",
      "how": "helper"
    },
    {
      "event": "digest.scheduled",
      "function": "BuilderService.handle_builder",
      "file": "./shop/reports/builders.py",
      "how": "direct"
    },
    {
      "event": "digest.scheduled",
      "function": "BuilderService.handle_builder",
      "file": "./shop/reports/builders.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "digest.shared",
      "function": "RetentionService.apply_retention",
      "file": "shop\\reports\\retention.py",
      "how": "direct"
    },
    {
      "event": "dispute.added",
      "function": "process_refund",
      "file": "shop/payments/refunds.py",
      "how": "direct"
    },
    {
      "event": "dispute.declined",
      "function": "start_capture",
      "file": "./shop/payments/capture.py",
      "how": "via constant"
    },
    {
      "event": "dispute.refunded",
      "function": "update_ledger",
      "file": "shop\\payments\\ledger.py",
      "how": "via constant"
    },
    {
      "event": "dispute.sent",
      "function": "close_capture",
      "file": "shop/payments/capture.py",
      "how": "helper"
    },
    {
      "event": "export.deleted",
      "function": "RetentionService.handle_retention",
      "file": "./shop/reports/retention.py",
      "how": "direct"
    },
    {
      "event": "export.exported",
      "function": "BuilderService.handle_builder",
      "file": "shop\\reports\\builders.py",
      "how": "direct"
    },
    {
      "event": "export.sent",
      "function": "process_sharing",
      "file": "shop/reports/sharing.py",
      "how": "direct"
    },
    {
      "event": "failure.accounts_locked",
      "function": "apply_session",
      "file": "./shop/accounts/sessions.py",
      "how": "direct"
    },
    {
      "event": "failure.catalog_published",
      "function": "handle_pricing",
      "file": "shop\\catalog\\pricing.py",
      "how": "direct"
    },
    {
      "event": "failure.catalog_published",
      "function": "handle_pricing",
      "file": "shop\\catalog\\pricing.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "failure.inventory_approved",
      "function": "BinService.start_bin",
      "file": "shop/inventory/bins.py",
      "how": "direct"
    },
    {
      "event": "failure.inventory_reserved",
      "function": "finish_writeoff",
      "file": "./shop/inventory/writeoffs.py",
      "how": "direct"
    },
    {
      "event": "failure.orders_confirmed",
      "function": "CartService.sync_cart",
      "file": "shop\\orders\\carts.py",
      "how": "direct"
    },
    {
      "event": "failure.payments_opened",
      "function": "close_capture",
      "file": "shop/payments/capture.py",
      "how": "helper"
    },
    {
      "event": "failure.reports_scheduled",
      "function": "process_sharing",
      "file": "./shop/reports/sharing.py",
      "how": "helper"
    },
    {
      "event": "failure.shipping_printed",
      "function": "ReturnService.record_return",
      "file": "shop\\shipping\\returns.py",
      "how": "direct"
    },
    {
      "event": "failure.support_opened",
      "function": "EscalationService.record_escalation",
      "file": "shop/support/escalation.py",
      "how": "via constant"
    },
    {
      "event": "image.created",
      "function": "CategorieService.handle_categorie",
      "file": "./shop/catalog/categories.py",
      "how": "via constant"
    },
    {
      "event": "image.renamed",
      "function": "CategorieService.record_categorie",
      "file": "shop\\catalog\\categories.py",
      "how": "via constant"
    },
    {
      "event": "image.repriced",
      "function": "process_media",
      "file": "shop/catalog/media.py",
      "how": "direct"
    },
    {
      "event": "image.repriced",
      "function": "process_media",
      "file": "shop/catalog/media.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "invite.changed",
      "function": "finish_invite",
      "file": "./shop/accounts/invites.py",
      "how": "direct"
    },
    {
      "event": "invite.revoked",
      "function": "UserService.sync_user",
      "file": "shop\\accounts\\users.py",
      "how": "via constant"
    },
    {
      "event": "invoice.cancelled",
      "function": "NoteService.process_note",
      "file": "shop/orders/notes.py",
      "how": "direct"
    },
    {
      "event": "invoice.merged",
      "function": "NoteService.close_note",
      "file": "./shop/orders/notes.py",
      "how": "helper"
    },
    {
      "event": "invoice.split",
      "function": "CartService.record_cart",
      "file": "shop\\orders\\carts.py",
      "how": "helper"
    },
    {
      "event": "item.adjusted",
      "function": "start_transfer",
      "file": "shop/inventory/transfers.py",
      "how": "direct"
    },
    {
      "event": "item.moved",
      "function": "update_transfer",
      "file": "./shop/inventory/transfers.py",
      "how": "helper"
    },
    {
      "event": "item.released",
      "function": "process_alert",
      "file": "shop\\inventory\\alerts.py",
      "how": "helper"
    },
    {
      "event": "item.written_off",
      "function": "BinService.start_bin",
      "file": "shop/inventory/bins.py",
      "how": "direct"
    },
    {
      "event": "label.delivered",
      "function": "record_pickup",
      "file": "./shop/shipping/pickups.py",
      "how": "direct"
    },
    {
      "event": "label.delivered",
      "function": "record_pickup",
      "file": "./shop/shipping/pickups.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "label.dispatched",
      "function": "close_rate",
      "file": "shop\\shipping\\rates.py",
      "how": "helper"
    },
    {
      "event": "label.printed",
      "function": "close_rate",
      "file": "shop/shipping/rates.py",
      "how": "direct"
    },
    {
      "event": "listing.renamed",
      "function": "CategorieService.handle_categorie",
      "file": "./shop/catalog/categories.py",
      "how": "helper"
    },
    {
      "event": "macro.opened",
      "function": "MacroService.record_macro",
      "file": "shop\\support\\macros.py",
      "how": "helper"
    },
    {
      "event": "macro.reassigned",
      "function": "record_ticket",
      "file": "shop/support/tickets.py",
      "how": "via constant"
    },
    {
      "event": "macro.sent",
      "function": "sync_note",
      "file": "./shop/support/notes.py",
      "how": "via constant"
    },
    {
      "event": "note.opened",
      "function": "finish_callback",
      "file": "shop\\support\\callbacks.py",
      "how": "direct"
    },
    {
      "event": "note.sent",
      "function": "start_note",
      "file": "shop/support/notes.py",
      "how": "via constant"
    },
    {
      "event": "order.merged",
      "function": "ServiceService.record_service",
      "file": "./shop/orders/service.py",
      "how": "via constant"
    },
    {
      "event": "parcel.booked",
      "function": "ReturnService.process_return",
      "file": "shop\\shipping\\returns.py",
      "how": "via constant"
    },
    {
      "event": "parcel.booked",
      "function": "ReturnService.process_return",
      "file": "shop\\shipping\\returns.py",
      "how": "via constant",
      "note": "written twice in this function"
    },
    {
      "event": "parcel.delivered",
      "function": "close_carrier",
      "file": "shop/shipping/carriers.py",
      "how": "helper"
    },
    {
      "event": "parcel.switched",
      "function": "TrackingService.sync_tracking",
      "file": "./shop/shipping/tracking.py",
      "how": "direct"
    },
    {
      "event": "password.created",
      "function": "ProfileService.process_profile",
      "file": "shop\\accounts\\profiles.py",
      "how": "helper"
    },
    {
      "event": "password.granted",
      "function": "PasswordService.process_password",
      "file": "shop/accounts/passwords.py",
      "how": "helper"
    },
    {
      "event": "payment.added",
      "function": "RetrieService.update_retrie",
      "file": "./shop/payments/retries.py",
      "how": "helper"
    },
    {
      "event": "payment.refunded",
      "function": "RetrieService.check_retrie",
      "file": "shop\\payments\\retries.py",
      "how": "helper"
    },
    {
      "event": "payment.retried",
      "function": "DisputeService.check_dispute",
      "file": "shop/payments/disputes.py",
      "how": "via constant"
    },
    {
      "event": "payment.sent",
      "function": "PayoutService.update_payout",
      "file": "./shop/payments/payouts.py",
      "how": "helper"
    },
    {
      "event": "payout.declined",
      "function": "RetrieService.close_retrie",
      "file": "shop\\payments\\retries.py",
      "how": "direct"
    },
    {
      "event": "payout.refunded",
      "function": "check_refund",
      "file": "shop/payments/refunds.py",
      "how": "helper"
    },
    {
      "event": "payout.refunded",
      "function": "check_refund",
      "file": "shop/payments/refunds.py",
      "how": "helper",
      "note": "written twice in this function"
    },
    {
      "event": "payout.removed",
      "function": "DisputeService.handle_dispute",
      "file": "./shop/payments/disputes.py",
      "how": "helper"
    },
    {
      "event": "payout.sent",
      "function": "update_refund",
      "file": "shop\\payments\\refunds.py",
      "how": "via constant"
    },
    {
      "event": "pickup.switched",
      "function": "ReturnService.process_return",
      "file": "shop/shipping/returns.py",
      "how": "direct"
    },
    {
      "event": "price.hidden",
      "function": "ImportService.start_import",
      "file": "./shop/catalog/imports.py",
      "how": "direct"
    },
    {
      "event": "price.imported",
      "function": "apply_product",
      "file": "shop\\catalog\\products.py",
      "how": "helper"
    },
    {
      "event": "price.renamed",
      "function": "ImportService.record_import",
      "file": "shop/catalog/imports.py",
      "how": "direct"
    },
    {
      "event": "product.archived",
      "function": "check_search",
      "file": "./shop/catalog/search.py",
      "how": "via constant"
    },
    {
      "event": "product.hidden",
      "function": "handle_media",
      "file": "shop\\catalog\\media.py",
      "how": "helper"
    },
    {
      "event": "product.renamed",
      "function": "CategorieService.start_categorie",
      "file": "shop/catalog/categories.py",
      "how": "helper"
    },
    {
      "event": "product.repriced",
      "function": "apply_product",
      "file": "./shop/catalog/products.py",
      "how": "direct"
    },
    {
      "event": "product.repriced",
      "function": "apply_product",
      "file": "./shop/catalog/products.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "refund.declined",
      "function": "handle_ledger",
      "file": "shop\\payments\\ledger.py",
      "how": "via constant"
    },
    {
      "event": "refund.retried",
      "function": "CardService.apply_card",
      "file": "shop/payments/cards.py",
      "how": "helper"
    },
    {
      "event": "refund.sent",
      "function": "CardService.apply_card",
      "file": "./shop/payments/cards.py",
      "how": "helper"
    },
    {
      "event": "report.generated",
      "function": "ScheduleService.handle_schedule",
      "file": "shop\\reports\\schedules.py",
      "how": "helper"
    },
    {
      "event": "report.scheduled",
      "function": "check_digest",
      "file": "shop/reports/digest.py",
      "how": "via constant"
    },
    {
      "event": "return.cancelled",
      "function": "CartService.record_cart",
      "file": "./shop/orders/carts.py",
      "how": "direct"
    },
    {
      "event": "role.locked",
      "function": "handle_session",
      "file": "shop\\accounts\\sessions.py",
      "how": "helper"
    },
    {
      "event": "role.opened",
      "function": "process_role",
      "file": "shop/accounts/roles.py",
      "how": "direct"
    },
    {
      "event": "role.revoked",
      "function": "LockoutService.process_lockout",
      "file": "./shop/accounts/lockout.py",
      "how": "via constant"
    },
    {
      "event": "role.unlocked",
      "function": "handle_role",
      "file": "shop\\accounts\\roles.py",
      "how": "direct"
    },
    {
      "event": "role.unlocked",
      "function": "handle_role",
      "file": "shop\\accounts\\roles.py",
      "how": "direct",
      "note": "written twice in this function"
    },
    {
      "event": "schedule.deleted",
      "function": "RetentionService.start_retention",
      "file": "shop/reports/retention.py",
      "how": "via constant"
    },
    {
      "event": "schedule.exported",
      "function": "DashboardService.sync_dashboard",
      "file": "./shop/reports/dashboards.py",
      "how": "direct"
    },
    {
      "event": "schedule.generated",
      "function": "ExportService.process_export",
      "file": "shop\\reports\\exports.py",
      "how": "direct"
    },
    {
      "event": "schedule.sent",
      "function": "DashboardService.sync_dashboard",
      "file": "shop/reports/dashboards.py",
      "how": "direct"
    },
    {
      "event": "session.changed",
      "function": "PasswordService.process_password",
      "file": "./shop/accounts/passwords.py",
      "how": "helper"
    },
    {
      "event": "session.created",
      "function": "process_role",
      "file": "shop\\accounts\\roles.py",
      "how": "helper"
    },
    {
      "event": "session.opened",
      "function": "LockoutService.process_lockout",
      "file": "shop/accounts/lockout.py",
      "how": "via constant"
    },
    {
      "event": "session.reset",
      "function": "record_session",
      "file": "./shop/accounts/sessions.py",
      "how": "helper"
    },
    {
      "event": "shipment.booked",
      "function": "ShipmentService.finish_shipment",
      "file": "shop\\shipping\\shipments.py",
      "how": "via constant"
    },
    {
      "event": "shipment.returned",
      "function": "TrackingService.sync_tracking",
      "file": "shop/shipping/tracking.py",
      "how": "helper"
    },
    {
      "event": "shipment.returned",
      "function": "TrackingService.sync_tracking",
      "file": "shop/shipping/tracking.py",
      "how": "helper",
      "note": "written twice in this function"
    },
    {
      "event": "shipment.switched",
      "function": "close_pickup",
      "file": "./shop/shipping/pickups.py",
      "how": "helper"
    },
    {
      "event": "shipment.voided",
      "function": "LabelService.apply_label",
      "file": "shop\\shipping\\labels.py",
      "how": "direct"
    },
    {
      "event": "stock.released",
      "function": "start_writeoff",
      "file": "shop/inventory/writeoffs.py",
      "how": "via constant"
    },
    {
      "event": "survey.booked",
      "function": "process_sla",
      "file": "./shop/support/sla.py",
      "how": "via constant"
    },
    {
      "event": "survey.sent",
      "function": "EscalationService.finish_escalation",
      "file": "shop\\support\\escalation.py",
      "how": "helper"
    },
    {
      "event": "ticket.escalated",
      "function": "sync_ticket",
      "file": "shop/support/tickets.py",
      "how": "helper"
    },
    {
      "event": "ticket.reassigned",
      "function": "MacroService.start_macro",
      "file": "./shop/support/macros.py",
      "how": "direct"
    },
    {
      "event": "transfer.reserved",
      "function": "CountService.start_count",
      "file": "shop\\inventory\\counts.py",
      "how": "helper"
    },
    {
      "event": "transfer.written_off",
      "function": "apply_alert",
      "file": "shop/inventory/alerts.py",
      "how": "direct"
    }
  ],
  "count": 120
}
