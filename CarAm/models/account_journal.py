# -*- coding: utf-8 -*-

from odoo import models, fields


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    is_used_for_subscriptions = fields.Boolean(
        string='Used for Subscriptions',
        default=False,
        help='Check this box if this journal is used for subscription invoices'
    )
    
    journal_sub_type = fields.Selection([
        ('bank', 'Bank'),
        ('fund', 'Fund'),
        ('cash', 'Cash'),
        ('tele', 'Tele')
    ], string="Wallet Type", help="Used to categorize journals for wallet transaction")

