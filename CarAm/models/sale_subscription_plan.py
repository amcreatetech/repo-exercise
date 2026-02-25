# -*- coding: utf-8 -*-

from odoo import models, fields


class SaleSubscriptionPlan(models.Model):
    _inherit = 'sale.subscription.plan'

    caram_subscription_type = fields.Selection(
        selection=[
            ('private', 'Private'),
            ('pinky', 'Pinky'),
            ('vip', 'VIP'),
            ('van', 'Van'),
            ('taxi', 'Taxi'),
            ('other', 'Other'),
        ],
        string='Subscription Type',
        required=True,
        help='Subscription type for CarAm system'
    )

