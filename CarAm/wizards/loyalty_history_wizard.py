# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CaramLoyaltyHistoryWizard(models.TransientModel):
    _name = "caram.loyalty.history.wizard"
    _description = "Create Loyalty History Entry"

    card_id = fields.Many2one("loyalty.card", required=True, ondelete="cascade")
    partner_id = fields.Many2one(related="card_id.partner_id", readonly=True)
    source_type = fields.Selection(
        [
            ("payment", "Payment"),
            ("invoice", "Invoice"),
            ("credit_note", "Credit Note"),
        ],
        string="Model",
        required=True,
        default="payment",
    )

    payment_id = fields.Many2one(
        "account.payment",
        domain="[('partner_id', '=', partner_id)]",
    )
    invoice_id = fields.Many2one(
        "account.move",
        domain="[('partner_id', '=', partner_id), ('move_type', '=', 'out_invoice')]",
    )
    credit_note_id = fields.Many2one(
        "account.move",
        domain="[('partner_id', '=', partner_id), ('move_type', '=', 'out_refund')]",
    )

    description = fields.Char(required=True)
    issued = fields.Float(string="Issued", required=True)
    status = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted")],
        required=True,
    )

    deposit_method = fields.Selection(
        [("direct", "Direct"), ("bank_transfer", "Bank Transfer")],
        string="Transaction Type",
        required=True,
        default="direct",
    )
    reference = fields.Char(string="Reference")
    bank = fields.Char(string="Bank")
    account_number = fields.Char(string="Account Number")

    @api.onchange("source_type")
    def _onchange_source_type(self):
        for wiz in self:
            wiz.payment_id = False
            wiz.invoice_id = False
            wiz.credit_note_id = False

    def _get_order_link(self):
        self.ensure_one()

        if self.source_type == "payment":
            if not self.payment_id:
                raise UserError(_("Please select a payment."))
            return "account.payment", self.payment_id.id

        if self.source_type == "invoice":
            if not self.invoice_id:
                raise UserError(_("Please select an invoice."))
            return "account.move", self.invoice_id.id

        if self.source_type == "credit_note":
            if not self.credit_note_id:
                raise UserError(_("Please select a credit note."))
            return "account.move", self.credit_note_id.id

        raise UserError(_("Unsupported model selection."))

    def action_confirm(self):
        self.ensure_one()
        
        order_model, order_id = self._get_order_link()

        history_vals = {
            "card_id": self.card_id.id,
            "description": self.description,
            "issued": self.issued,
            "used": 0.0,
            "status": self.status,
            "deposit_method": self.deposit_method,
            "reference": self.reference or "",
            "bank": self.bank or "",
            "account_number": self.account_number or "",
            "order_model": order_model,
            "order_id": order_id,
        }
        history = self.env["loyalty.history"].create(history_vals)
        new_balance = self.card_id.caram_get_posted_balance()
        self.card_id.write({"points": new_balance})

        return {
            "type": "ir.actions.act_window",
            "name": _("Loyalty History"),
            "res_model": "loyalty.history",
            "view_mode": "form",
            "res_id": history.id,
        }


