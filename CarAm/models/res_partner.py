# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError



class ResPartner(models.Model):
    _inherit = 'res.partner'

    sub_id = fields.Char(
        string='Platform ID',
        help='External platform identifier (Sub ID)',
        copy=False,
        index=True
    )
    
    gender = fields.Selection([
        ('male', 'Male'),
        ('female', 'Female'),
    ], string='Gender')
    
    contact_type = fields.Selection([
        ('driver', 'Driver'),
        ('rider', 'Rider'),
    ], string='Contact Type')
    
 
    
    @api.constrains('sub_id', 'company_id')
    def _check_unique_sub_id(self):
        """Ensure sub_id is unique per company"""
        for record in self:
            if record.sub_id and record.company_id:
                existing = self.search([
                    ('sub_id', '=', record.sub_id),
                    ('company_id', '=', record.company_id.id),
                    ('id', '!=', record.id)
                ], limit=1)
                if existing:
                    raise ValidationError(
                        f"A contact with Platform ID '{record.sub_id}' already exists in this company."
                    )

