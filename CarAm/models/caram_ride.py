from odoo import fields, models, _
from odoo.exceptions import UserError
import logging
_logger = logging.getLogger(__name__)



class CaramCompensationProductConfig(models.Model):
    _name = "caram.compensation.product.config"
    _description = "Compensation Type -> Product mapping per company"
    _rec_name = "type"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    type = fields.Selection(
        [
            ("bonus", "Bonus"),
            ("driver_coupon", "Driver Coupon"),
            ("rider_coupon", "Rider Coupon"),
            ("fees", "Fees"),
            ("discount", "Discount"),
            ("expense", "Expense"),
            ("fine", "Fine"),
            ("commission", "Commission"),
        ],
        required=True,
    )
    product_id = fields.Many2one("product.product", required=True)

    _sql_constraints = [
        (
            "company_type_uniq",
            "unique(company_id, type)",
            "Only one product mapping allowed per type per company.",
        ),
    ]
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


    def _create_expense_bill(self, driver, amount, company_id, accounting_date=None):
        self.ensure_one()
        amount = float(amount or 0.0)
        comp_type = "expense"

        config = self.env["caram.compensation.product.config"].sudo().search(
            [("company_id", "=", company_id), ("type", "=", comp_type)],
            limit=1,
        )
        # إذا مالقيتش، دور على إعداد الأب (الشركة الأم)
        if not config:
            config = self.env["caram.compensation.product.config"].sudo().search(
                [("company_id", "parent_of", company_id), ("type", "=", comp_type)],
                limit=1,
            )

        if not config or not config.product_id:
            raise UserError(_(
                "Compensation product not configured for type '%s' on company id '%s'."
            ) % (comp_type, company_id))

        product = config.product_id.with_company(company_id)

        expense_account = (
            product.property_account_expense_id
            or product.categ_id.property_account_expense_categ_id
        )
        if not expense_account:
            raise UserError(_("No expense account configured for compensation product."))

        journal = self.env["account.journal"].sudo().with_company(company_id).search(
            [("type", "=", "purchase"),
            '|', ("company_id", "=", company_id), ("company_id", "parent_of", company_id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("Purchase journal is not configured for this company."))

        ref = f"Ride {self.ride_id} expense amount {amount} for external driver {driver.name}"
        move_date = accounting_date or fields.Date.context_today(self)

        move_vals = {
            "move_type": "in_invoice",
            "company_id": company_id,
            "journal_id": journal.id,
            "invoice_date": move_date,
            "partner_id": driver.id,
            "ref": ref,
            "invoice_line_ids": [
                (0, 0, {
                    "name": ref,
                    "product_id": product.id,
                    "quantity": 1,
                    "price_unit": amount,
                    "account_id": expense_account.id,
                }),
            ],
        }

        bill = self.env["account.move"].sudo().with_company(company_id).create(move_vals)
        bill.action_post()
        return bill

    def _create_expense_journal_entry(self, driver, amount, accounting_date=None):
        self.ensure_one()
        amount = float(amount or 0.0)
        journal = self.env["account.journal"].sudo().with_company(self.company_id.id).search(
            [("code", "=", "EXP"), '|', ('company_id', '=', self.company_id.id), ('company_id', 'parent_of', self.company_id.id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("Expense journal is not configured for this company."))
        ref = f"Ride {self.ride_id} expense amount {amount} for external driver {driver.name}"
        move_date = accounting_date or fields.Date.context_today(self)
        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,   
            "date": move_date,
            "ref": ref,
            "line_ids": [
                (0, 0, {
                    "name": ref,
                    "partner_id": driver.id,
                    "account_id": journal.expense_account_id.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
                   (0, 0, {
                    "name": ref,
                    "partner_id": driver.id,
                    "account_id": driver.property_account_expense_id.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
            ],
        }
        journal_entry = self.env["account.move"].sudo().with_company(self.company_id.id).create(move_vals)
        journal_entry.action_post()
        return journal_entry

    def _create_journal_entry(
        self, driver, rider, amount, accounting_date=None, note_from_api=False, api_payload=False , company_id=None
    ):
        """Create & post a journal entry transferring wallet amount rider -> driver.

        Uses company configured wallet liability accounts.
        """
        self.ensure_one()
        amount = float(amount or 0.0)
        if amount <= 0:
            raise UserError(_("amount must be greater than 0"))

        rider_wallet_account = self.rider_id.with_company(
            company_id or self.company_id.id
        ).property_account_receivable_id
        driver_wallet_account = self.driver_id.with_company(
            company_id or self.company_id.id
        ).property_account_receivable_id
        if not rider_wallet_account:
            raise UserError(_("Rider has no receivable account."))
        if not driver_wallet_account:
            raise UserError(_("Driver has no receivable account."))

        if self.env.context.get("caram_is_airport_trip"):
            
            journal = self.env["account.journal"].sudo().with_company(company_id).search(
                                    [("type", "=", "sale"), ("is_airport_journal", "!=", False),
                                    '|', ('company_id', '=', company_id or self.company_id.id), 
                                    ('company_id', 'parent_of', company_id or self.company_id.id)
                                    ], limit=1
                                )
            if not journal:
                raise UserError(_("Airport journal is not configured for this company. create journal entry"+company_id))
        else:
            journal = self.env["account.journal"].sudo().with_company(self.company_id.id).search(
                [("type", "=", "general"),  
            '|', ('company_id', '=', company_id or self.company_id.id), 
            ('company_id', 'parent_of', company_id or self.company_id.id)],
                limit=1,
            )
            if not journal:
                journal = self.env["account.journal"].sudo().with_company(self.company_id.id).search(
                    [
                        ("type", "=", "general"),
                        ("company_id", "parent_of", company_id or self.company_id.id),
                    ],
                    limit=1,
                )
        if not journal:
            raise UserError(_("No journal found to post CarAm wallet transfer entries."))

        ref = f"Ride {self.ride_id} wallet transfer"
        move_date = accounting_date or fields.Date.context_today(self)
        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,   
            "date": move_date,
            "ref": ref,
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
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
        """Wallet card for this ride's company (invoices follow card.company_id)."""
        self.ensure_one()
        return self.env["loyalty.card"].sudo().search(
            [
                ("partner_id", "=", partner.id)
            ],
            limit=1,
        )

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
    def action_pay_ride(self, *,fare_amount, wallet_paid, cash_paid, commission_amount, penalties, payment_mode, accounting_date=None, note_from_api=False, api_payload=False, is_airport_trip=False, driver_type=None, expense_amount=0.0, company_id=None):
        self.ensure_one()
        self = self.with_company(self.company_id.id).with_context(
            allowed_company_ids=[self.company_id.id],
            caram_is_airport_trip=is_airport_trip,
        )
        
        if self.state == "paid":
            raise UserError(_("Ride already paid."))

        doc_date = accounting_date or fields.Date.context_today(self)
        api_note = note_from_api or False
        stored_api_payload = api_payload or False

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
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
                company_id=company_id,
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
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
                )

            rider_wallet_delta = 0.0
            driver_wallet_delta = -commission_amount

        #feda edit - in case of cash exceed, the extra amount is deposited to rider wallet and commission + fine is withdrawn from driver wallet
        elif payment_mode == "cash_exceed": 
            extra = cash_paid - self.fare_amount
            resp = rider_card.caram_wallet_clearing(
                extra,
                rider=self.rider_id,
                driver=self.driver_id,
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
            )
            _logger.info(f"Cash exceed case: cash_paid={cash_paid}, fare_amount={self.fare_amount}, extra={extra}. Wallet clearing done.")
            _logger.info(f"caram_wallet_clearing responce {resp}")
            driver_card.caram_withdraw(
                commission_amount + driver_penalty_amount,
                commission_amount,
                fine_amount=driver_penalty_amount,
                description=f"Ride commission {self.driver_id} (cash)",
                status="posted",
                driver=self.driver_id,
                should_create_invoice=True,
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
                company_id=company_id,
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
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
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
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
                company_id=company_id,
            )

            history2 = driver_card.caram_addwallet(
                wallet_paid,
                description=f"Driver wallet amount {self.driver_id} (wallet)",
                status="posted",
                driver=self.driver_id,
                should_create_payment=False,
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
            )
            # Create Journal Entery
            # to transfer from rider wallet to driver wallet
            journal_entry = self._create_journal_entry(
                self.driver_id,
                self.rider_id,
                wallet_paid,
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
                company_id=company_id,
            )
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
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
                company_id=company_id,
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
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
                )

            rider_wallet_delta = -self.fare_amount
            driver_wallet_delta = float(self.fare_amount - commission_amount)

        elif payment_mode == "wallet_cash":
            if wallet_paid > 0:
                journal_entry = self._create_journal_entry(
                    self.driver_id,
                    self.rider_id,
                    wallet_paid,
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
                )
                history1 = rider_card.caram_withdraw(
                    wallet_paid,
                    rider_penalty_amount,
                    fine_amount=driver_penalty_amount,
                    description=f"Ride wallet amount {self.ride_id} (wallet part)",
                    status="posted",
                    driver=self.rider_id,
                    should_create_invoice=False,
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
                )
                history2 = driver_card.caram_addwallet(
                    wallet_paid,
                    description=f"Driver wallet amount {self.ride_id} (wallet part)",
                    status="posted",
                    driver=self.driver_id,
                    should_create_payment=False,
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
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
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                )
                driver_card.caram_addwallet(
                    -due_amount,
                    description=f"Ride wallet amount {self.ride_id} (cash part)",
                    status="posted",
                    driver=self.driver_id,
                    should_create_payment=True,
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
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
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
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
                    accounting_date=doc_date,
                    note_from_api=api_note,
                    api_payload=stored_api_payload,
                    company_id=company_id,
                )

            rider_wallet_delta = -wallet_paid
            driver_wallet_delta = float(wallet_paid - commission_amount)

        else:
            raise UserError(_("Invalid payment_mode"))

        if driver_type == 'external':
            journal_entry = self._create_expense_bill(
                self.driver_id,
                float(expense_amount or 0.0),
                company_id=self.company_id.id,
                accounting_date=doc_date,
            )
            card = (self.env["loyalty.card"].sudo().search( [("partner_id", "=", self.driver_id.id)],
                    limit=1,))
            if not card:
                raise UserError(_("Wallet not found for driver."))
            history_vals = {
                "card_id": card.id,
                "description": f"Ride expense amount {self.ride_id} (external driver)",
                "issued": float(expense_amount or 0.0),
                "used": 0.0,
                "status": "posted",
                "order_model": "account.move",
                "order_id": journal_entry.id,
                "transaction_date": accounting_date or fields.Datetime.now(),
            }
            tx = self.env["loyalty.history"].sudo().create(history_vals)

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