# -*- coding: utf-8 -*-

from odoo import models, _, fields
from odoo.exceptions import UserError


class LoyaltyCard(models.Model):
    _inherit = "loyalty.card"


    def _create_invoice_from_lines(self, partner_id, invoice_line_vals_list):
        """Create & post an out_invoice for partner with provided invoice lines."""
        self.ensure_one()
        if not partner_id:
            raise UserError(_("Missing partner to invoice."))
        if not invoice_line_vals_list:
            raise UserError(_("Missing invoice lines."))

        invoice = (
            self.env["account.move"]
            .sudo()
            .with_company(self.company_id.id)
            .create(
                {
                    "invoice_date": fields.Date.today(),
                    "journal_id": self._get_general_journal(),
                    "move_type": "out_invoice",
                    "partner_id": partner_id.id,
                    "invoice_line_ids": [(0, 0, vals) for vals in invoice_line_vals_list],
                    "is_from_api": True,
                }
            )
        )
        invoice.action_post()
        return invoice

    def _prepare_commission_invoice_line_vals(self, amount):
        self.ensure_one()
        commission_product = self.company_id.caram_commission_product_id
        if not commission_product:
            raise UserError(_("Please set commission product in the settings !"))
        return {
            "product_id": commission_product.id,
            "name": _("Ride Commission"),
            "quantity": 1,
            "price_unit": amount,
        }

    def _prepare_fine_invoice_line_vals(self, amount):
        self.ensure_one()
        fine_product = self.company_id.caram_fine_product_id
        if not fine_product:
            raise UserError(_("Please set fine product in the settings !"))
        return {
            "product_id": fine_product.id,
            "name": _("Ride Fine"),
            "quantity": 1,
            "price_unit": amount,
        }

    def create_points_credit_note(self, env, company_id, partner, amount):
        """Create & post a customer credit note to represent the welcome coupon."""
        points_product = env['product.product'].sudo().with_company(company_id).search(
            [('is_points', '=', True)],
            limit=1,
        )
        if not points_product:
            return False

        expense_account = (
            points_product.property_account_expense_id
            or points_product.categ_id.property_account_expense_id
        )
        if not expense_account:
            expense_account = env['account.account'].sudo().with_company(company_id).search(
                [('company_id', '=', company_id), ('account_type', 'in', ('expense'))],
                limit=1,
            )
        if not expense_account:
            return False

        credit_note = env['account.move'].sudo().with_company(company_id).create({
            'partner_id': partner.id,
            
            'move_type': 'out_refund',
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [(0, 0, {
                'product_id': points_product.id,
                'account_id': expense_account.id,
                'name': 'Loyality program - points Credit',
                'quantity': 1,
                'price_unit': amount,
            })],
            'is_from_api': True,
            })
        
        # Post the credit note to make it effective
        credit_note.action_post()
        return credit_note

    def _create_payment(
        self,
        partner,
        amount,
        payment_method_type,
        ref,
        *,
        should_post=True,
        transaction_id=None,
        image_url=None,
        bank=None,
        account_number=None,
    ):
        """Create an account.payment and optionally post it.

        Returns: (payment_record | None, error_message | None)
        """
        self.ensure_one()
        if not partner:
            return None, _("Missing partner for payment.")
        if not hasattr(partner, "id"):
            return None, _("Invalid partner value for payment.")

        journal = self.env["account.journal"].sudo().search(
            [
                ("journal_sub_type", "=", payment_method_type),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not journal:
            return None, _("No journal found for %s") % (payment_method_type,)

        payment_vals = {
            "payment_type": "inbound" if amount > 0 else "outbound",
            "partner_id": partner.id,
            "partner_type": "customer",
            "amount": abs(amount),
            "journal_id": journal.id,
            "memo": ref,
            "caram_transaction_id": transaction_id,
            "caram_image_url": image_url,
            "caram_bank": bank,
            "caram_account_number": account_number,
            "is_from_api": True,
            }

        payment = (
            self.env["account.payment"]
            .sudo()
            .with_company(self.company_id.id)
            .create(payment_vals)
        )
        if payment and should_post:
            payment.action_post()

        return payment, None

    def _get_general_journal(self):
        self.ensure_one()
        journal = self.company_id.caram_wallet_journal_id
        if journal:
            return journal.id
        journal = self.env["account.journal"].sudo().with_company(self.company_id.id).search(
            [("company_id", "=", self.company_id.id), ("type", "=", "sale")], limit=1
        )
        if not journal:
            raise UserError(_("No general journal found to post CarAm ride accounting entries."))
        return journal.id

    def caram_get_posted_balance(self):
        """Return wallet balance based on posted loyalty history: sum(issued) - sum(used)."""
        self.ensure_one()
        posted_history = self.env["loyalty.history"].sudo().search(
            [("card_id", "=", self.id), ("status", "=", "posted")]
        )
        total_issued = sum(posted_history.mapped("issued") or [0.0])
        total_used = sum(posted_history.mapped("used") or [0.0])
      
        return total_issued - total_used

    def caram_withdraw(
        self,
        amount,
        commission_amount=0.0,
        fine_amount=0.0,
        *,
        description="",
        status="posted",
        driver=None,
        should_create_invoice=True,
        order_model=None,
        order_id=None,
    ): 
        self.ensure_one()
        amount = float(amount or 0.0)
        commission_amount = float(commission_amount or 0.0)
        fine_amount = float(fine_amount or 0.0)
        #if amount <= 0:
            #raise UserError(_("amount must be greater than 0"))

        balance_before = self.caram_get_posted_balance()
        
        should_invoice = should_create_invoice and (commission_amount >= 0 or fine_amount > 0)
        if should_invoice:
            invoice_lines = []
            if commission_amount >= 0:
                invoice_lines.append(self._prepare_commission_invoice_line_vals(commission_amount))
            if fine_amount > 0:
                invoice_lines.append(self._prepare_fine_invoice_line_vals(fine_amount))
            invoice = self._create_invoice_from_lines(driver, invoice_lines)
        else:
            invoice = None
        tx = (
            self.env["loyalty.history"]
            .sudo()
            .create(
                {
                    "card_id": self.id,
                    "description": description or "",
                    "issued": -amount,
                    "used": 0.0,
                    "status": status or "posted",
                    "order_model": "account.move" if invoice else order_model,
                    "order_id":invoice.id if invoice else order_id,
                }
            )
        )
        
        balance_after = (
            self.caram_get_posted_balance() if (status or "posted") == "posted" else (balance_before - amount)
        )
        self.sudo().write({"points": balance_after})
        return tx

    def caram_addwallet(
        self,
        amount,
        *,
        description="",
        status="posted",
        driver=None,
        should_create_payment=True,
        order_model=None,
        order_id=None,
    ):
        self.ensure_one()
        amount = float(amount or 0.0)
        #if amount <= 0:
            #raise UserError(_("amount must be greater than 0"))
        if should_create_payment:
            payment, error = self._create_payment(
                driver,
                amount,
                "cash",
                description or "",
                should_post=True,
                transaction_id=None,
                image_url=None,
                bank=None,
                account_number=None,
            )
            if error:
                raise UserError(error)
        else:
            payment = None
         
        transaction_vals = {
            "card_id": self.id,
            "description": description or "",
            "issued": amount,
            "deposit_method": "direct",
            "reference": "",
            "bank": "",
            "account_number": "",
            "status": status or "posted",
            
        }
            
        if payment and hasattr(payment, "id"):
            transaction_vals.update({
                "order_model": "account.payment",
                "order_id": payment.id,
            })

        else:
            transaction_vals.update({
                "order_model": order_model if not order_model is None else "res.partner",
                "order_id": order_id if not order_id is None else driver.id ,
            })

        transaction = self.env['loyalty.history'].sudo().create(transaction_vals)
        total_balance = self.caram_get_posted_balance()
        # -------------------- Update Card Points --------------------
        self.sudo().write({"points": total_balance})

        return transaction