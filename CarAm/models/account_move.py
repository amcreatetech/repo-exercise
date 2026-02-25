# -*- coding: utf-8 -*-

from odoo import models, fields


class AccountMove(models.Model):
    _inherit = "account.move"

    is_from_api = fields.Boolean(
        string="Created from API",
        default=False,
        copy=False,
        help="Indicates if this invoice, credit note, or journal entry was created from API",
        readonly=True,
    )

