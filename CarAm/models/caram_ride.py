from requests import request

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
            ("promo_coupon", "Promo Coupon"),
            ("fees", "Fees"),
            ("discount", "Discount"),
            ("sales_discount", "Sales Discount"),
            ("expense", "Expense"),
            ("fine", "Fine"),
            ("commission", "Commission"),
            ("fleet_operation", "Fleet Operation"),
            ("operational_fine", "Operational Fine"),
            ("employees_fine", "Employees Fine"),
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

    has_wallet_entries = fields.Boolean(default=False, readonly=True)

    fare_amount = fields.Monetary(required=True, readonly=True)
    wallet_paid = fields.Monetary(default=0.0, readonly=True)
    cash_paid = fields.Monetary(default=0.0, readonly=True)
    commission_amount = fields.Monetary(default=0.0, readonly=True)
    discount_percent = fields.Float(default=0.0, readonly=True)
    discount_amount = fields.Monetary(default=0.0, readonly=True)
    payment_mode = fields.Selection(
        [
            ("cash_only", "Cash"),
            ("cash_exceed", "Cash Exceed"),
            ("wallet_paid", "Wallet Paid"),
            ("wallet_cash", "Wallet and Cash"),
        ],
        readonly=True, string='Payment Mode'
    )
    state = fields.Selection([("draft", "Draft"), ("paid", "Paid")], default="draft", index=True)
    paid_at = fields.Datetime(readonly=True)
    expense_amount = fields.Monetary(default=0.0, readonly=True)
    driver_type = fields.Selection([("internal", "Internal"), ("external", "External")], default="internal", readonly=True, string='Driver Type')
    is_airport_trip = fields.Boolean(default=False, readonly=True)
    note_from_api = fields.Text(readonly=True)
    api_payload = fields.Text(readonly=True)
    coupon_value = fields.Monetary(default=0.0, readonly=True)
    coupon_description = fields.Text(readonly=True)
    coupon_credit_note_id = fields.Many2one("account.move", readonly=True)

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


    def _create_penalty_journal_entry(
        self, comp_type, partner, amount, description, company_id,
        accounting_date, note_from_api, api_payload, partner_account_field,
):
        """
        Posts:  Dr <partner_account_field>   amount
                Cr <penalty product's income account>   amount
        Mirrors the wallet_compensation pattern, but credits a revenue
        account instead of debiting an expense account, since a penalty
        is income for the company rather than a cost.
        """
        env = self.env

        config = env["caram.compensation.product.config"].sudo().search(
            [("company_id", "=", company_id), ("type", "=", comp_type)],
            limit=1,
        )
        if not config:
            config = env["caram.compensation.product.config"].sudo().search(
                [("company_id", "parent_of", company_id), ("type", "=", comp_type)],
                limit=1,
            )
        if not config or not config.product_id:
            raise UserError(_("Penalty product not configured for type '%s'") % comp_type)

        product = config.product_id.with_company(company_id)
        revenue_account = (
            product.property_account_income_id
            or product.categ_id.property_account_income_categ_id
        )
        if not revenue_account:
            raise UserError(_("No revenue account configured for penalty product '%s'") % comp_type)

        journal = env["account.journal"].sudo().with_company(company_id).search(
            [("type", "=", "general"), '|',
            ("company_id", "=", company_id), ("company_id", "parent_of", company_id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("No journal found to post CarAm penalty entries."))

        partner_account = partner.with_company(company_id)[partner_account_field]
        if not partner_account:
            raise UserError(_("Partner has no %s account configured") % partner_account_field)

        ref = description
        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,
            "date": accounting_date,
            "ref": ref,
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "line_ids": [
                (0, 0, {
                    "name": ref,
                    "partner_id": partner.id,
                    "account_id": partner_account.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "name": ref,
                    "partner_id": partner.id,
                    "product_id": product.id,
                    "account_id": revenue_account.id,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ],
        }
        move = env["account.move"].sudo().with_company(company_id).create(move_vals)
        move.action_post()
        return move

    def _get_expense_account(self, env, company_id, comp_type):
        config = env["caram.compensation.product.config"].sudo().search(
            [("company_id", "=", company_id), ("type", "=", comp_type)], limit=1,
        )
        if not config:
            config = env["caram.compensation.product.config"].sudo().search(
                [("company_id", "parent_of", company_id), ("type", "=", comp_type)], limit=1,
            )
        if not config or not config.product_id:
            company = env["res.company"].sudo().browse(company_id)
            raise UserError(_("Expense product not configured for type '%s' on company '%s'") % (comp_type, company.name))

        product = config.product_id.with_company(company_id)
        expense_account = (
            product.property_account_expense_id
            or product.categ_id.property_account_expense_categ_id
        )
        if not expense_account:
            raise UserError(_("No expense account configured for product (type '%s')") % comp_type)
        return expense_account

    def _get_compensation_income_account(self, env, company_id, comp_type):
        """يجيب حساب الإيراد حسب نوع التعويض (commission / fleet_operation)
        مع fallback على إعداد الشركة الأم."""
        config = env["caram.compensation.product.config"].sudo().search(
            [("company_id", "=", company_id), ("type", "=", comp_type)],
            limit=1,
        )
        if not config:
            config = env["caram.compensation.product.config"].sudo().search(
                [("company_id", "parent_of", company_id), ("type", "=", comp_type)],
                limit=1,
            )

        if not config or not config.product_id:
            company = env["res.company"].sudo().browse(company_id)
            raise UserError(_(
                "Compensation product not configured for type '%s' on company '%s'"
            ) % (comp_type, company.name))

        product = config.product_id.with_company(company_id)
        income_account = (
            product.property_account_income_id
            or product.categ_id.property_account_income_categ_id
        )
        if not income_account:
            raise UserError(_(
                "No income account configured for compensation product (type '%s')"
            ) % comp_type)

        return income_account
    
    def _get_ride_journal(self, env, company_id, ride_code="cash_only"):
        journal = env["account.journal"].sudo().search([
            ("company_id", "=", company_id),
            ("type", "=", "general"),
            ("ride_code", "=", ride_code),
        ], limit=1)
        if not journal:
            raise UserError(_("Cash ride settlement journal not configured for this company"))
        return journal

    def _build_promo_ride_journal_entry(self, env, company_id, currency_id, driver, driver_type,
                                     fare_amount, commission_amount,
                                     accounting_date, note_from_api, api_payload,
                                     auto_post=False):

        currency = env["res.currency"].sudo().browse(currency_id) if currency_id else env["res.company"].sudo().browse(company_id).currency_id

        marketing_expense_account = self._get_expense_account(env, company_id, "promo_coupon")

        line_vals = [(0, 0, {
            "name": f"Promo coupon marketing expense - ride {self.ride_id}",
            "account_id": marketing_expense_account.id,
            "debit": fare_amount,
            "credit": 0.0,
            "currency_id": currency.id,
            "amount_currency": fare_amount,
        })]

        if driver_type == "internal":
            fleet_revenue_account = self._get_compensation_account(env, company_id, "fleet_operation")
            line_vals.append((0, 0, {
                "name": f"Company fleet operation revenue - ride {self.ride_id}",
                "account_id": fleet_revenue_account.id,
                "debit": 0.0,
                "credit": fare_amount,
                "currency_id": currency.id,
                "amount_currency": -fare_amount,
            }))

        elif driver_type == "external":
            driver_payable_amount = fare_amount - commission_amount
            if driver_payable_amount < 0:
                raise UserError(_("Commission amount cannot exceed fare amount"))

            commission_account = self._get_compensation_account(env, company_id, "commission")
            driver_payable_account = driver.with_company(company_id).property_account_payable_id
            if not driver_payable_account:
                raise UserError(_("Driver has no payable account configured"))

            line_vals += [
                (0, 0, {
                    "name": f"Ride commission revenue - ride {self.ride_id}",
                    "account_id": commission_account.id,
                    "debit": 0.0,
                    "credit": commission_amount,
                    "currency_id": currency.id,
                    "amount_currency": -commission_amount,
                }),
                (0, 0, {
                    "name": f"Driver operating payable - ride {self.ride_id}",
                    "account_id": driver_payable_account.id,
                    "debit": 0.0,
                    "credit": driver_payable_amount,
                    "partner_id": driver.id,
                    "currency_id": currency.id,
                    "amount_currency": -driver_payable_amount,
                }),
            ]
        else:
            raise UserError(_("Unknown driver type: %s") % driver_type)

        journal = self._get_ride_journal(env, company_id, ride_code="promo_free_ride")

        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,
            "ref": f"Ride {self.ride_id} promo free ride",
            "driver_id": driver.id,
            "ride_id": self.ride_id,
            "date": accounting_date,
            "currency_id": currency.id,
            "state": "draft",
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "line_ids": line_vals,
        }

        move = env["account.move"].sudo().with_company(company_id).create(move_vals)
        if auto_post:
            move.action_post()
        return move

    def _build_driver_discount_invoice(self, env, company_id, currency_id, driver, driver_type,
                                    fare_amount, commission_amount, discount_percent,
                                    accounting_date, note_from_api, api_payload,
                                    auto_post_invoice=False):

        if driver_type != "external":
            raise UserError(_("Airport coupon discount currently applies only to external drivers"))

        driver_payable_amount = fare_amount - commission_amount
        if driver_payable_amount < 0:
            raise UserError(_("Commission amount cannot exceed fare amount"))

        commission_account = self._get_compensation_account(env, company_id, "commission")
        driver_payable_account = driver.with_company(company_id).property_account_payable_id
        if not driver_payable_account:
            raise UserError(_("Driver has no payable account configured"))

        invoice_lines = [
            (0, 0, {
                "name": f"Ride commission revenue - ride {self.ride_id}",
                "account_id": commission_account.id,
                "quantity": 1,
                "price_unit": commission_amount,
                "discount": discount_percent,
            }),
            (0, 0, {
                "name": f"Driver operating payable - ride {self.ride_id}",
                "account_id": driver_payable_account.id,
                "partner_id": driver.id,
                "quantity": 1,
                "price_unit": driver_payable_amount,
                "discount": discount_percent,
            }),
        ]

        journal = self._get_ride_journal(env, company_id, ride_code="airport_discount")

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": driver.id,
            "journal_id": journal.id,
            "driver_id": driver.id,
            "ride_id": self.ride_id,
            "invoice_date": accounting_date,
            "currency_id": currency_id,
            "state": "draft",
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "invoice_line_ids": invoice_lines,
        }

        invoice = env["account.move"].sudo().with_company(company_id).create(move_vals)
        if auto_post_invoice:
            invoice.action_post()
        return invoice
    
    def _build_admin_discount_invoice(self, env, company_id, currency_id, driver, driver_type,
                                   discount_amount, fare_amount, commission_amount,
                                   accounting_date, note_from_api, api_payload,
                                   auto_post_invoice=False):

        if driver_type != "external":
            raise UserError(_("Admin sales discount currently applies only to external drivers"))

        driver_payable_amount = fare_amount - commission_amount
        if driver_payable_amount < 0:
            raise UserError(_("Commission amount cannot exceed fare amount"))

        if discount_amount > fare_amount:
            raise UserError(_("Discount amount cannot exceed fare amount"))

        commission_account = self._get_compensation_account(env, company_id, "commission")
        driver_payable_account = driver.with_company(company_id).property_account_payable_id
        if not driver_payable_account:
            raise UserError(_("Driver has no payable account configured"))
        discount_expense_account = self._get_expense_account(env, company_id, "sales_discount")

        invoice_lines = [
            (0, 0, {
                "name": f"Ride commission revenue - ride {self.ride_id}",
                "account_id": commission_account.id,
                "quantity": 1,
                "price_unit": commission_amount,
            }),
            (0, 0, {
                "name": f"Driver operating payable - ride {self.ride_id}",
                "account_id": driver_payable_account.id,
                "partner_id": driver.id,
                "quantity": 1,
                "price_unit": driver_payable_amount,
            }),
            (0, 0, {
                "name": f"Admin sales discount (company-absorbed) - ride {self.ride_id}",
                "account_id": discount_expense_account.id,
                "quantity": 1,
                "price_unit": -discount_amount,   # ← سطر سالب: بيصير debit على حساب المصروف
            }),
        ]

        journal = self._get_ride_journal(env, company_id, ride_code="admin_sales_discount")

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": driver.id,
            "journal_id": journal.id,
            "driver_id": driver.id,
            "ride_id": self.ride_id,
            "invoice_date": accounting_date,
            "currency_id": currency_id,
            "state": "draft",
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "invoice_line_ids": invoice_lines,
        }

        invoice = env["account.move"].sudo().with_company(company_id).create(move_vals)
        if auto_post_invoice:
            invoice.action_post()
        return invoice
    
    def _build_cash_only_ride_journal_entry(self, env, company_id, currency_id, driver, driver_type,
                               fare_amount, commission_amount,
                               accounting_date, note_from_api, api_payload,
                               auto_post=False):

        custody_account = driver.with_company(company_id).property_account_receivable_id
        if not custody_account:
            raise UserError(_("Driver has no custody account configured"))

        # سطر العهدة النقدية - ثابت بكل الحالات
        line_vals = [(0, 0, {
            "name": f"Driver cash custody - ride {self.ride_id}",
            "account_id": custody_account.id,
            "debit": fare_amount,
            "credit": 0.0,
            "partner_id": driver.id,
            "currency_id": currency_id,
            "amount_currency": fare_amount,
        })]

        if driver_type == "internal":
            # السائق موظف: كل المبلغ إيراد تشغيل أسطول، ما في عمولة ولا مستحقات
            fleet_revenue_account = self._get_compensation_account(
                env, company_id, "fleet_operation"
            )
            line_vals.append((0, 0, {
                "name": f"Company fleet operation revenue - ride {self.ride_id}",
                "account_id": fleet_revenue_account.id,
                "debit": 0.0,
                "credit": fare_amount,
                "currency_id": currency_id,
                "amount_currency": -fare_amount,
            }))

        elif driver_type == "external":
            driver_payable_amount = fare_amount - commission_amount
            if driver_payable_amount < 0:
                raise UserError(_("Commission amount cannot exceed fare amount"))

            commission_account = self._get_compensation_account(
                env, company_id, "commission"
            )
            driver_payable_account = driver.with_company(company_id).property_account_payable_id
            if not driver_payable_account:
                raise UserError(_("Driver partner has no payable account configured"))

            line_vals += [
                (0, 0, {
                    "name": f"Ride commission revenue - ride {self.ride_id}",
                    "account_id": commission_account.id,
                    "debit": 0.0,
                    "credit": commission_amount,
                    "currency_id": currency_id,
                    "amount_currency": -commission_amount,
                }),
                (0, 0, {
                    "name": f"Driver operating payable - ride {self.ride_id}",
                    "account_id": driver_payable_account.id,
                    "debit": 0.0,
                    "credit": driver_payable_amount,
                    "partner_id": driver.id,
                    "currency_id": currency_id,
                    "amount_currency": -driver_payable_amount,
                }),
            ]
        else:
            raise UserError(_("Unknown driver type: %s") % driver_type)

        journal = self._get_ride_journal(env, company_id, ride_code="cash_only")

        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,
            "ref": f"Ride {self.ride_id} cash settlement",
            "driver_id": driver.id,
            "ride_id": self.ride_id,
            "date": accounting_date,
            "state": "draft",
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "line_ids": line_vals,
        }

        move = env["account.move"].sudo().with_company(company_id).create(move_vals)
        if auto_post:
            move.action_post()
        return move

    def _build_wallet_ride_invoice(self, env, company_id, currency_id, rider, driver, driver_type,
                                fare_amount, commission_amount,
                                accounting_date, note_from_api, api_payload,
                                auto_post_invoice=False):

        if driver_type == "external":
            driver_payable_amount = fare_amount - commission_amount
            if driver_payable_amount < 0:
                raise UserError(_("Commission amount cannot exceed fare amount"))

            commission_account = self._get_compensation_account(env, company_id, "commission")
            driver_payable_account = driver.with_company(company_id).property_account_payable_id
            if not driver_payable_account:
                raise UserError(_("Driver has no payable account configured"))

            invoice_lines = [
                (0, 0, {
                    "name": f"Ride commission revenue - ride {self.ride_id}",
                    "account_id": commission_account.id,
                    "quantity": 1,
                    "price_unit": commission_amount,
                }),
                (0, 0, {
                    "name": f"Driver wallet payable - ride {self.ride_id}",
                    "account_id": driver_payable_account.id,
                    "partner_id": driver.id,          # ✅ لازم صراحة، الفاتورة الأصلية partner_id = raker مش السائق
                    "quantity": 1,
                    "price_unit": driver_payable_amount,
                }),
            ]
        elif driver_type == "internal":
            fleet_revenue_account = self._get_compensation_account(env, company_id, "fleet_operation")
            invoice_lines = [(0, 0, {
                "name": f"Company fleet operation revenue - ride {self.ride_id}",
                "account_id": fleet_revenue_account.id,
                "quantity": 1,
                "price_unit": fare_amount,
            })]
        else:
            raise UserError(_("Unknown driver type: %s") % driver_type)

        journal = self._get_journal_by_code(env, company_id, "wallet_only")

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": rider.id,
            "journal_id": journal.id,
            "driver_id": driver.id,
            "ride_id": self.ride_id,
            "invoice_date": accounting_date,
            "state": "draft",
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "invoice_line_ids": invoice_lines,
            "currency_id": currency_id,
        }

        invoice = env["account.move"].sudo().with_company(company_id).create(move_vals)
        if auto_post_invoice:
            invoice.action_post()
        return invoice

    def _build_mixed_ride_journal_entry(self, env, company_id, currency_id, rider, driver, driver_type,
                                     rider_wallet_amount, driver_cash_amount,
                                     fare_amount, commission_amount,
                                     accounting_date, note_from_api, api_payload,
                                     auto_post=False):

        if rider_wallet_amount + driver_cash_amount != fare_amount:
            raise UserError(_(
                "Rider wallet amount (%s) + driver cash amount (%s) must equal fare amount (%s)"
            ) % (rider_wallet_amount, driver_cash_amount, fare_amount))

        rider_wallet_account = rider.with_company(company_id).property_account_receivable_id
        if not rider_wallet_account:
            raise UserError(_("Rider has no receivable account configured"))

        driver_custody_account = driver.with_company(company_id).property_account_receivable_id
        if not driver_custody_account:
            raise UserError(_("Driver has no receivable account configured"))

        line_vals = [
            (0, 0, {
                "name": f"Rider wallet payment (partial) - ride {self.ride_id}",
                "account_id": rider_wallet_account.id,
                "debit": rider_wallet_amount,
                "credit": 0.0,
                "currency_id": currency_id,
                "amount_currency": rider_wallet_amount,
                "partner_id": rider.id,
            }),
            (0, 0, {
                "name": f"Driver cash custody (partial) - ride {self.ride_id}",
                "account_id": driver_custody_account.id,
                "debit": driver_cash_amount,
                "credit": 0.0,
                "currency_id": currency_id,
                "amount_currency": driver_cash_amount,
                "partner_id": driver.id,
            }),
        ]

        if driver_type == "internal":
            fleet_revenue_account = self._get_compensation_account(env, company_id, "fleet_operation")
            line_vals.append((0, 0, {
                "name": f"Company fleet operation revenue - ride {self.ride_id}",
                "account_id": fleet_revenue_account.id,
                "debit": 0.0,
                "credit": fare_amount,
                "currency_id": currency_id,
                "amount_currency": -fare_amount,
            }))

        elif driver_type == "external":
            driver_payable_amount = fare_amount - commission_amount
            if driver_payable_amount < 0:
                raise UserError(_("Commission amount cannot exceed fare amount"))

            commission_account = self._get_compensation_account(env, company_id, "commission")
            driver_payable_account = driver.with_company(company_id).property_account_payable_id
            if not driver_payable_account:
                raise UserError(_("Driver has no payable account configured"))

            line_vals += [
                (0, 0, {
                    "name": f"Ride commission revenue - ride {self.ride_id}",
                    "account_id": commission_account.id,
                    "debit": 0.0,
                    "credit": commission_amount,
                    "currency_id": currency_id,
                    "amount_currency": -commission_amount,
                }),
                (0, 0, {
                    "name": f"Driver operating payable - ride {self.ride_id}",
                    "account_id": driver_payable_account.id,
                    "debit": 0.0,
                    "credit": driver_payable_amount,
                    "partner_id": driver.id,
                    "currency_id": currency_id,
                    "amount_currency": -driver_payable_amount,
                }),
            ]
        else:
            raise UserError(_("Unknown driver type: %s") % driver_type)

        journal = self._get_journal_by_code(env, company_id, "cash_wallet")

        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,
            "ref": f"Ride {self.ride_id} mixed settlement",
            "driver_id": driver.id,
            "ride_id": self.ride_id,
            "date": accounting_date,
            "state": "draft",
            "is_from_api": True,
            "note_from_api": note_from_api or False,
            "api_payload": api_payload or False,
            "line_ids": line_vals,
        }

        move = env["account.move"].sudo().with_company(company_id).create(move_vals)
        if auto_post:
            move.action_post()
        return move
    # ---------------------------
    # Main payment logic
    # ---------------------------
    def action_pay_ride(self, *,fare_amount, wallet_paid, cash_paid, commission_amount, payment_mode, accounting_date=None, note_from_api=False, api_payload=False, is_airport_trip=False, driver_type=None, expense_amount=0.0, company_id=None, currency_id=None):
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
        

        company = self.env["res.company"].sudo().browse(company_id) if company_id else self.company_id
        currency = self.env["res.currency"].sudo().browse(currency_id) if currency_id else company.currency_id

       
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
            move = self._build_cash_only_ride_journal_entry(self.env, company_id, currency.id, self.driver_id, driver_type, fare_amount, commission_amount,
                                accounting_date, api_note, stored_api_payload,
                                auto_post=False,)
            
            rider_wallet_delta = 0.0
            driver_wallet_delta = -(commission_amount)

        elif payment_mode == "wallet_paid":
                    move = self._build_wallet_ride_journal_entry(
                    self.env, company_id, currency.id, self.rider_id, self.driver_id, driver_type,
                    fare_amount, commission_amount,
                    doc_date, api_note, stored_api_payload,
                    auto_post=False,)
                    # تحديث فعلي لأرصدة wallet.card يصير بمكان منفصل لاحقًا (out of scope هون)
                    rider_wallet_delta = -fare_amount
                    driver_wallet_delta = fare_amount - (commission_amount) if driver_type == "external" else 0.0
        
        elif payment_mode == "wallet_cash":
                    move = self._build_mixed_ride_journal_entry(
                        self.env, company_id, currency.id, self.rider_id, self.driver_id, driver_type,
                        wallet_paid, cash_paid,
                        fare_amount, commission_amount,
                        doc_date, api_note, stored_api_payload,
                        auto_post=False,)
        
                    rider_wallet_delta = -wallet_paid
                    driver_wallet_delta = -(commission_amount) if driver_type == "external" else 0.0
        
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
                currency_id=currency.id,
            )
            _logger.info(f"Cash exceed case: cash_paid={cash_paid}, fare_amount={self.fare_amount}, extra={extra}. Wallet clearing done.")
            _logger.info(f"caram_wallet_clearing responce {resp}")
            driver_card.caram_withdraw(
                commission_amount,
                commission_amount,
                fine_amount=0,
                description=f"Ride commission {self.driver_id} (cash)",
                status="posted",
                driver=self.driver_id,
                should_create_invoice=True,
                accounting_date=doc_date,
                note_from_api=api_note,
                api_payload=stored_api_payload,
                company_id=company_id,
            )

            # cash_paid > fare_amount => diff is deposited to rider wallet
            rider_wallet_delta = float(cash_paid - self.fare_amount)
            driver_wallet_delta = -commission_amount

                
        else:
            raise UserError(_("Invalid payment_mode"))

        response = {
            "status": "success",
            "ride_id": self.ride_id,
            "case": case,
            "currency": currency.name,
            "wallet_movements": {
                "rider_wallet_delta": rider_wallet_delta,
                "driver_wallet_delta": driver_wallet_delta,
            },
            "commission": {
                "amount": commission_amount,
                "invoiced": bool(commission_amount and commission_amount > 0),
            },
            
        }
        return response

    