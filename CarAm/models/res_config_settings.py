# -*- coding: utf-8 -*-

from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'
    
   
   

    caram_rider_receivable_account_id = fields.Many2one(
        'account.account',
        string='Rider Receivable Account',
        domain="[('account_type', '=', 'asset_receivable')]",
        help='Default receivable for Riders',
    )
    caram_rider_payable_account_id = fields.Many2one(
        'account.account',
        string='Rider Payable Account',
        domain="[('account_type', '=', 'liability_payable')]",
        help='Default payable for Riders',
    )
    caram_driver_receivable_account_id = fields.Many2one(
        'account.account',
        string='Driver Receivable Account',
        domain="[('account_type', '=', 'asset_receivable')]",
        help='Default receivable for Drivers',
    )
    caram_driver_payable_account_id = fields.Many2one(
        'account.account',
        string='Driver Payable Account',
        domain="[('account_type', '=', 'liability_payable')]",
        help='Default payable for Drivers',
    )




class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'


    caram_rider_receivable_account_id = fields.Many2one(
        related='company_id.caram_rider_receivable_account_id',
        readonly=False,
    )
    caram_rider_payable_account_id = fields.Many2one(
        related='company_id.caram_rider_payable_account_id',
        readonly=False,
    )
    caram_driver_receivable_account_id = fields.Many2one(
        related='company_id.caram_driver_receivable_account_id',
        readonly=False,
    )
    caram_driver_payable_account_id = fields.Many2one(
        related='company_id.caram_driver_payable_account_id',
        readonly=False,
    )

    caram_api_base_url = fields.Char(
        config_parameter='caram.api.base.url',
        default='https://staging.caram.app',
        help='Base URL for CarAm API (use staging.caram.app for testing, backend.caram.app for production)'
    )