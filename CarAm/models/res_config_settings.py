# -*- coding: utf-8 -*-

from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    caram_bank_account_id = fields.Many2one(
        'account.account',
        string='Bank Account',
        domain="[('account_type', '=', 'asset_cash')]",
        help='Default bank account for CarAm transactions (Bank & Cash type accounts only)'
    )
    
    caram_bouns_account_id = fields.Many2one(
        'account.account',
        string='bouns & discount Account',
        help='Default bank account for CarAm transactions (Bank & Cash type accounts only)'
    )
    
    caram_rider_wallets_account_id = fields.Many2one(
        'account.account',
        string='Rider Wallets Account',
        domain="[('account_type', '=', 'liability_current')]",
        help='Account for rider wallet transactions'
    )
    
    caram_driver_wallet_account_id = fields.Many2one(
        'account.account',
        string='Driver Wallet Account',
        domain="[('account_type', '=', 'liability_current')]",
        help='Account for driver wallet transactions'
    )
    
    caram_mobile_payment_services_account_id = fields.Many2one(
        'account.account',
        string='mobile payment services Account',
        
        help='Account for mobile payment services'
    )
    
    caram_point_expense_account_id = fields.Many2one(
        'account.account',
        string='point expense Account',
        help='Account for Charge by Point conversion'
    )
    
    caram_fine_revenue_account_id = fields.Many2one(
        'account.account',
        string='fine revenue Account',
        help='Account for fine revenue'
    )

    caram_wallet_journal_id = fields.Many2one(
        "account.journal",
        string="CarAm Wallet Journal",
        domain="[('type', '=', 'sale')]",
        help="General journal used to post wallet transfers / penalties for CarAm rides.",
    )

    caram_commission_product_id = fields.Many2one(
        "product.product",
        string="Commission Product",
        help="Product used on commission invoices for driver rides.",
    )

    caram_fine_product_id = fields.Many2one(
        "product.product",
        string="Fine Product",
        help="Product used on fine/penalty invoices for CarAm rides.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    caram_bank_account_id = fields.Many2one(
        related='company_id.caram_bank_account_id',
        readonly=False,
    )
    
    caram_bouns_account_id = fields.Many2one(
        related='company_id.caram_bouns_account_id',
        readonly=False,
    )
    
    caram_rider_wallets_account_id = fields.Many2one(
        related='company_id.caram_rider_wallets_account_id',
        readonly=False,
    )
    
    caram_driver_wallet_account_id = fields.Many2one(
        related='company_id.caram_driver_wallet_account_id',
        readonly=False,
    )
    
    caram_mobile_payment_services_account_id = fields.Many2one(
        related='company_id.caram_mobile_payment_services_account_id',
        readonly=False,
    )
    
    caram_point_expense_account_id = fields.Many2one(
       related='company_id.caram_point_expense_account_id',
       readonly=False,
    )
    
    caram_fine_revenue_account_id = fields.Many2one(
        related='company_id.caram_fine_revenue_account_id',
        readonly=False,
    )

    caram_wallet_journal_id = fields.Many2one(
        related="company_id.caram_wallet_journal_id",
        readonly=False,
    )

    caram_commission_product_id = fields.Many2one(
        related="company_id.caram_commission_product_id",
        readonly=False,
    )

    caram_fine_product_id = fields.Many2one(
        related="company_id.caram_fine_product_id",
        readonly=False,
    )
    
    caram_api_base_url = fields.Char(
        config_parameter='caram.api.base.url',
        default='https://staging.caram.app',
        help='Base URL for CarAm API (use staging.caram.app for testing, backend.caram.app for production)'
    )