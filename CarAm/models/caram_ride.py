# -*- coding: utf-8 -*-

from odoo import fields, models, _
from odoo.exceptions import UserError
import logging
_logger = logging.getLogger(__name__)

class CaramRide(models.Model):
    _name = "caram.ride"
    _description = "CarAm Ride"
    _rec_name = "ride_id"

    _sql_constraints = [
        ("ride_id_company_uniq", "unique(ride_id, company_id)", "Ride ID must be unique per company."),
    ]

    ride_id = fields.Char(required=True, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id", store=True, readonly=True)

    rider_id = fields.Many2one("res.partner", string="Rider", required=True, readonly=True)
    driver_id = fields.Many2one("res.partner", string="Driver", required=True, readonly=True)

    fare_amount = fields.Monetary(required=True, readonly=True)
    wallet_paid = fields.Monetary(default=0.0, readonly=True)
    cash_paid = fields.Monetary(default=0.0, readonly=True)
    commission_amount = fields.Monetary(default=0.0, readonly=True)
    payment_mode = fields.Selection(
        [
            ("cash", "Cash"),
            ("wallet", "Wallet"),
            ("mixed", "Mixed"),
        ],
        readonly=True,
    )
    state = fields.Selection([("draft", "Draft"), ("paid", "Paid")], default="draft", index=True)
    paid_at = fields.Datetime(readonly=True)

    def _create_journal_entry(self, driver, rider, amount):
        """Create & post a journal entry transferring wallet amount rider -> driver.

        Uses company configured wallet liability accounts.
        """
        self.ensure_one()
        amount = float(amount or 0.0)
        if amount <= 0:
            raise UserError(_("amount must be greater than 0"))

        rider_wallet_account = self.driver_id.property_account_receivable_id
        driver_wallet_account = self.rider_id.property_account_receivable_id
        if not rider_wallet_account:
            raise UserError(_("Rider has no receivable account."))
        if not driver_wallet_account:
            raise UserError(_("Driver has no receivable account."))

        journal = self.env["account.journal"].sudo().with_company(self.company_id.id).search(
            [("company_id", "=", self.company_id.id), ("type", "=", "general")],
            limit=1,
        )
        if not journal:
            raise UserError(_("No journal found to post CarAm wallet transfer entries."))

        ref = f"Ride {self.ride_id} wallet transfer"
        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,   
            "date": fields.Date.context_today(self),
            "ref": ref,
            "is_from_api": True,
            "line_ids": [
                (0, 0, {
                    "name": ref,
                    "partner_id": rider.id,
                    "account_id": rider_wallet_account.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "name": ref,
                    "partner_id": driver.id,
                    "account_id": driver_wallet_account.id,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ],
        }

        journal_entry = self.env["account.move"].sudo().with_company(self.company_id.id).create(move_vals)
        journal_entry.action_post()
        return journal_entry
        

    def _get_wallet_card(self, partner):
        return self.env["loyalty.card"].sudo().search([("partner_id", "=", partner.id)], limit=1)

    def _get_receivable_account(self, partner):
        account = partner.with_company(self.company_id.id).property_account_receivable_id
        if account:
            return account
        account = self.env["account.account"].sudo().with_company(self.company_id.id).search(
            [("company_id", "=", self.company_id.id), ("account_type", "=", "asset_receivable")], limit=1
        )
        if not account:
            raise UserError(_("No receivable account configured for penalties."))
        return account


    # ---------------------------
    # Main payment logic
    # ---------------------------
    def action_pay_ride(self, *,fare_amount, wallet_paid, cash_paid, commission_amount, penalties, payment_mode):
        self.ensure_one()
        if self.state == "paid":
            raise UserError(_("Ride already paid."))

        wallet_paid = float(wallet_paid or 0.0)
        cash_paid = float(cash_paid or 0.0)
        commission_amount = float(commission_amount or 0.0)
        payment_mode = payment_mode
        fare_amount = float(fare_amount or 0.0)
        penalties = penalties or []

        # Penalties can be for driver / rider / both
        driver_penalty_amount = 0.0
        rider_penalty_amount = 0.0
        for p in penalties:
            if not isinstance(p, dict):
                continue
            party = (p.get("party") or "").strip().lower()
            amount = float(p.get("amount") or 0.0)
            if amount <= 0:
                continue
            if party == "driver":
                driver_penalty_amount += amount
            elif party == "rider":
                rider_penalty_amount += amount

        # Response fields (API contract)
        case_map = {
            "cash_only": "CASH_ONLY",
            "cash_exceed": "CASH_EXCEED",
            "wallet_paid": "WALLET_ONLY",
            "wallet_cash": "WALLET_PLUS_CASH",
        }
        case = case_map.get(payment_mode, payment_mode or "")

        # Wallet movements are reported as net deltas (what should happen economically)
        rider_wallet_delta = 0.0
        driver_wallet_delta = 0.0
        
        # Cards (wallets)
        rider_card = self._get_wallet_card(self.rider_id)
        if not rider_card:
            raise UserError(_("Wallet not found for rider."))

        driver_card = self._get_wallet_card(self.driver_id)
        if not driver_card:
            raise UserError(_("Wallet not found for driver."))

        # Add fine to rider and driver if exist  
        if payment_mode == "cash_only":
            driver_card.caram_withdraw(
                commission_amount + driver_penalty_amount,
                commission_amount,
                fine_amount=driver_penalty_amount,
                description=f"Ride commission {self.ride_id} (cash)",
                status="posted",
                driver=self.driver_id,
                should_create_invoice=True,
            )
            if rider_penalty_amount > 0:
                rider_card.caram_withdraw(
                    rider_penalty_amount,
                    commission_amount= 0.0,
                    fine_amount=rider_penalty_amount,
                    description=f"Ride penalty {self.ride_id} (rider)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_invoice=True,
                )

            rider_wallet_delta = 0.0
            driver_wallet_delta = -commission_amount

        elif payment_mode == "cash_exceed":
            extra = cash_paid - self.fare_amount
            rider_card.caram_addwallet(
                extra,
                description=f"Ride payment {self.ride_id} (wallet)",
                status="posted",
                driver=self.rider_id,
                should_create_payment=True,
            )
            
            driver_card.caram_addwallet(
                -extra,
                description=f"Ride payment {self.driver_id} (wallet)",
                status="posted",
                driver=self.driver_id,
                should_create_payment=True,
            )
            driver_card.caram_withdraw(
                commission_amount + driver_penalty_amount,
                commission_amount,
                fine_amount=driver_penalty_amount,
                description=f"Ride commission {self.driver_id} (cash)",
                status="posted",
                driver=self.driver_id,
                should_create_invoice=True,
            )
            if rider_penalty_amount > 0:
                rider_card.caram_withdraw(
                    rider_penalty_amount,
                    commission_amount= 0.0,
                    fine_amount=rider_penalty_amount,
                    description=f"Ride penalty {self.ride_id} (rider)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_invoice=True,
                )

            # cash_paid > fare_amount => diff is deposited to rider wallet
            rider_wallet_delta = float(cash_paid - self.fare_amount)
            driver_wallet_delta = -commission_amount

        elif payment_mode == "wallet_paid":
            history1 = rider_card.caram_withdraw(
                wallet_paid,
                rider_penalty_amount,
                fine_amount=driver_penalty_amount,
                description=f"Ride wallet amount {self.ride_id} (wallet)",
                status="posted",
                driver=self.rider_id,
                should_create_invoice=False,
            )

            history2 = driver_card.caram_addwallet(
                wallet_paid,
                description=f"Driver wallet amount {self.driver_id} (wallet)",
                status="posted",
                driver=self.driver_id,
                should_create_payment=False,
            )
            # Create Journal Entery
            # to transfer from rider wallet to driver wallet
            journal_entry = self._create_journal_entry(self.driver_id, self.rider_id, wallet_paid)
            history1.sudo().write({
                "order_model": "account.move",
                "order_id": journal_entry.id,
            })
            history2.sudo().write({
                "order_model": "account.move",
                "order_id": journal_entry.id,
            })
            driver_card.caram_withdraw(
                commission_amount + driver_penalty_amount,
                commission_amount,
                fine_amount=driver_penalty_amount,
                description=f"Ride commission {self.ride_id} (cash)",
                status="posted",
                driver=self.driver_id,
                should_create_invoice=True,
            )
            if rider_penalty_amount > 0:
                rider_card.caram_withdraw(
                    rider_penalty_amount,
                    commission_amount= 0.0,
                    fine_amount=rider_penalty_amount,
                    description=f"Ride penalty {self.ride_id} (rider)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_invoice=True,
                )

            rider_wallet_delta = -self.fare_amount
            driver_wallet_delta = float(self.fare_amount - commission_amount)

        elif payment_mode == "wallet_cash":
            if wallet_paid > 0:
                journal_entry = self._create_journal_entry(self.driver_id, self.rider_id, wallet_paid)
                history1 = rider_card.caram_withdraw(
                    wallet_paid,
                    rider_penalty_amount,
                    fine_amount=driver_penalty_amount,
                    description=f"Ride wallet amount {self.ride_id} (wallet part)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_invoice=False,
                )
                history2 = driver_card.caram_addwallet(
                    wallet_paid,
                    description=f"Driver wallet amount {self.ride_id} (wallet part)",
                    status="posted",
                    driver=self.driver_id,
                    should_create_payment=False,
                )
                history1.sudo().write({
                    "order_model": "account.move",
                    "order_id": journal_entry.id,
                })
                history2.sudo().write({
                    "order_model": "account.move",
                    "order_id": journal_entry.id,
                })
            diff = fare_amount - wallet_paid
         
            if cash_paid > diff:
                due_amount = cash_paid - diff
                rider_card.caram_addwallet(
                    due_amount,
                    description=f"Ride wallet amount {self.ride_id} (cash part)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_payment=True,
                )
                driver_card.caram_addwallet(
                    -due_amount,
                    description=f"Ride wallet amount {self.ride_id} (cash part)",
                    status="posted",
                    driver=self.driver_id,
                    should_create_payment=True,
                )
              
            if commission_amount >= 0 or driver_penalty_amount>=0:
                driver_card.caram_withdraw(
                    commission_amount + driver_penalty_amount,
                    commission_amount,
                    fine_amount=driver_penalty_amount,
                    description=f"Ride commission {self.ride_id} (wallet+cash)",
                    status="posted",
                    driver=self.driver_id,
                    should_create_invoice=True,
                )
            if rider_penalty_amount > 0:
                rider_card.caram_withdraw(
                    rider_penalty_amount,
                    commission_amount= 0.0,
                    fine_amount=rider_penalty_amount,
                    description=f"Ride penalty {self.ride_id} (rider)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_invoice=True,
                )

            rider_wallet_delta = -wallet_paid
            driver_wallet_delta = float(wallet_paid - commission_amount)

        else:
            raise UserError(_("Invalid payment_mode"))

        response = {
            "status": "success",
            "ride_id": self.ride_id,
            "case": case,
            "wallet_movements": {
                "rider_wallet_delta": rider_wallet_delta,
                "driver_wallet_delta": driver_wallet_delta,
            },
            "commission": {
                "amount": commission_amount,
                "invoiced": bool(commission_amount and commission_amount > 0),
            },
            "penalties_applied": bool(driver_penalty_amount or rider_penalty_amount),
        }
        return response