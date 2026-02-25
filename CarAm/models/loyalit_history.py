# -*- coding: utf-8 -*-
from odoo import models, fields

class LoyaltyHistory(models.Model):
    _inherit = "loyalty.history"

    deposit_method = fields.Selection([
        ('direct', 'Direct'),
        ('bank_transfer', 'Bank Transfer')
    ], string="Transaction Type", required=True, default='direct')
    reference = fields.Char(string="Reference")
    bank = fields.Char(string="Bank")
    status = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted')
    ], string="Status", default='draft')
    
    account_number = fields.Char(
        string='Account Number',
        help='Customer bank account number'
    )