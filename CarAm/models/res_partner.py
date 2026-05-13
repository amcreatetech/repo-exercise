import requests

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
import logging
log = logging.getLogger(__name__)

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
     

    billing_type = fields.Selection([
        ('commission', 'Commission'),
        ('subscription', 'Subscription'),
    ], string='Billing Type')

    wallet_balance = fields.Float(
        digits='Product Price',
        copy=False,
        help='Last wallet balance fetched from the CarAm platform API.',
    )

    def _get_caram_wallet_api_url(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'caram.api.base.url',
            'https://staging.caram.app',
        ).rstrip('/')
        if self.contact_type == 'rider':
            return f'{base_url}/api/odoo/users/rider/{self.id}'
        return f'{base_url}/api/odoo/users/driver/{self.id}'

    def _get_caram_api_headers(self):
        """Same as account.payment: Bearer token from caram.api.token when set."""
        headers = {'Accept': 'application/json'}
        token = self.env['ir.config_parameter'].sudo().get_param('caram.api.token')
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    def _send_caram_wallet_balance_update(self):
        api_url = self._get_caram_wallet_api_url()
        try:
            response = requests.get(
                api_url, timeout=10, headers=self._get_caram_api_headers()
            )
            response.raise_for_status()
            rows = response.json().get('data') or []
            log.info(f"List Of Rows: {rows}")
            if not isinstance(rows, list):
                raise UserError('Invalid API response from CarAm.')

            row = rows[0] if rows else None
            if not row or row.get('wallet_balance') is None:
                raise UserError('Wallet balance not found in CarAm response.')
            log.info(f"Wallet balance: {row['wallet_balance']}")
            self.write({'wallet_balance': float(row['wallet_balance'])})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f"Balance updated successfully: {row['wallet_balance']}",
                    'type': 'success',
                    'sticky': False,
                },
            }
        except requests.exceptions.HTTPError as e:
            raise UserError(f'Failed to fetch wallet balance from CarAm: {str(e)}')

    def action_caram_get_wallet_balance(self):
        return self._send_caram_wallet_balance_update()

    def _caram_apply_accounting_partner_accounts(self):
        """Set company-dependent AR/AP from CarAm settings when role is rider or driver."""
        for partner in self:
            company = partner.company_id or self.env.company
            if not company:
                continue
        
            role = partner.contact_type
            if not role:
                continue
            if role == 'rider':
                receivable = company.caram_rider_receivable_account_id
                payable = company.caram_rider_payable_account_id
                
            elif role == 'driver':
                receivable = company.caram_driver_receivable_account_id
                payable = company.caram_driver_payable_account_id
            if not receivable or not payable:
                continue

            partner.with_context(force_company=company.id).write({
                'property_account_receivable_id': receivable.id,
                'property_account_payable_id': payable.id,
            })

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners._caram_apply_accounting_partner_accounts()
        return partners

    
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

