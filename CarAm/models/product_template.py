# -*- coding: utf-8 -*-

from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_coupon = fields.Boolean(
        string="Is Coupon",
        default=False,
        help="Marks this product as a coupon product (used by CarAm welcome coupon logic).",
    )

    is_points = fields.Boolean(
        string="Is Points",
        default=False,
        help="Marks this product as a points product (used by CarAm Loyality logic).",
    )



