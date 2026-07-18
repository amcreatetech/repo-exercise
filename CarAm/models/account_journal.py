# -*- coding: utf-8 -*-

from odoo import models, fields


class WalletType(models.Model):
    _name = 'wallet.type'

    name = fields.Char(required=True)
    code = fields.Char()
    journal_ids = fields.One2many('account.journal', 'wallet_type_id')
    company_id = fields.Many2one('res.company')
    
class AccountJournal(models.Model):
    _inherit = 'account.journal'

    is_used_for_subscriptions = fields.Boolean(
        string='Used for Subscriptions',
        default=False,
        help='Check this box if this journal is used for subscription invoices'
    )
    
    wallet_type_id = fields.Many2one(
        'wallet.type',
        string="Wallet Type",
        help="Used to categorize journals for wallet transaction"
    )
    
    journal_sub_type = fields.Selection([
        ('bank', 'Bank'),
        ('fund', 'Fund'),
        ('cash', 'Cash'),
        ('tele', 'Tele')
    ], string="Wallet Type", help="Used to categorize journals for wallet transaction")

    expense_account_id = fields.Many2one('account.account', string="Expense Account", help="Used to categorize journals for expense transaction")

