# -*- coding: utf-8 -*-

from odoo import models, fields
from odoo.exceptions import UserError
from cryptography.fernet import Fernet
import base64
import requests
import logging

_logger = logging.getLogger(__name__)

class AccountPayment(models.Model):
    _inherit = "account.payment"

    caram_transaction_id = fields.Char("Transaction ID", help="Financial transfer number from external system", readonly=True)
    caram_image_url = fields.Char("Bank Notification Image URL", help="URL of the bank notification image")
    caram_bank = fields.Char("Bank", help="Bank name used for the transaction")
    caram_account_number = fields.Char("Customer bank Account Number", help="Customer bank account number")
    caram_decline_reason = fields.Text("Decline Reason", help="Reason for declining the transaction")
    caram_status_synced = fields.Boolean("Status Synced with CarAm", default=False, help="Indicates if the status has been synced with CarAm platform", readonly=True)
    caram_attachment = fields.Binary(
        string="Upload Attachment",
        attachment=True,
        copy=False,
        help="Upload a document related to this payment"
    )
    caram_attachment_name = fields.Char(
        string="File Name"
    )
    is_from_api = fields.Boolean(
        string="Created from API",
        default=False,
        help="Indicates if this payment was created from API",
        readonly=True,
        copy=False,
    )

    def _get_caram_api_url(self):
        """Get CarAm API base URL from settings"""

        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'caram.api.base.url', 
            'https://staging.caram.app'
        )
        return f"{base_url}/api/change-transaction-status"

    def _get_caram_api_headers(self):
        """Return headers for CarAm API requests.

        If `caram.api.token` is set in system parameters, it will be used as a Bearer token.
        """
        headers = {
            'Accept': 'application/json',
        }
        token = self.env['ir.config_parameter'].sudo().get_param('caram.api.token')
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    def _prepare_caram_status_payload(self, status):
        """Prepare CarAm payload for change-transaction-status endpoint including encrypted attachment."""

        self.ensure_one()

        # Validations
        if not status or status not in ['confirm', 'decline']:
            raise UserError('Only posted or cancelled entries can be synced with CarAm')
        if not self.caram_transaction_id:
            raise UserError('Transaction ID is required')

        payload = {
            'transaction_id': self.caram_transaction_id,
            'status': status,
        }

        if self.caram_bank:
            payload['bank'] = self.caram_bank
        if self.caram_account_number:
            payload['account_number'] = self.caram_account_number

        # Encrypt caram_attachment if exists
        if self.caram_attachment:
            encrypted_attachment = self._encrypt_attachment(self.caram_attachment)
            payload['attachment'] = encrypted_attachment
            payload['attachment_filename'] = self.caram_attachment_name or 'attachment'

        if status == 'decline':
            payload['decline_reason'] = self.caram_decline_reason or 'Transaction cancelled'
        _logger.info(f"Payload: {payload}")
        return payload

    def _prepare_caram_status_payload(self, status):
        """Prepare CarAm payload including encrypted attachment (AES)."""

        self.ensure_one()

        if status not in ['confirm', 'decline']:
            raise UserError('Invalid status')
        if not self.caram_transaction_id:
            raise UserError('Transaction ID is required')

        payload = {
            'transaction_id': self.caram_transaction_id,
            'status': status,
        }

        if self.caram_bank:
            payload['bank'] = self.caram_bank
        if self.caram_account_number:
            payload['account_number'] = self.caram_account_number

        # Encrypt attachment
        if self.caram_attachment:
            payload['attachment'] = self._encrypt_attachment(self.caram_attachment)
            payload['attachment_filename'] = self.caram_attachment_name or 'attachment'

        if status == 'decline':
            payload['decline_reason'] = self.caram_decline_reason or 'Transaction cancelled'

        _logger.info("CarAm payload prepared")
        return payload


    def _encrypt_attachment(self, attachment_binary):
        """Encrypt attachment using shared secret (AES / Fernet)."""

        self.ensure_one()

        secret = self.env['ir.config_parameter'].sudo().get_param('caram.shared_secret')
        if not secret:
            raise UserError("Missing shared secret for CarAm encryption")

        fernet = Fernet(secret.encode())

        file_bytes = base64.b64decode(attachment_binary)
        encrypted_bytes = fernet.encrypt(file_bytes)

        return base64.b64encode(encrypted_bytes).decode()

                

    def _send_caram_status_update(self, status):
        """Send status update to CarAm platform."""
        self.ensure_one()

        api_url = self._get_caram_api_url()
        payload = self._prepare_caram_status_payload(status)
        
        try:
            response = requests.post(api_url, json=payload, timeout=10, headers=self._get_caram_api_headers())
            response.raise_for_status()
            
            # Mark as synced
            self.sudo().write({'caram_status_synced': True})
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f'Status updated successfully on CarAm platform',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except requests.exceptions.HTTPError as e:
            raise UserError(f'Failed to update status on CarAm: {str(e)}')


    def action_post(self):
        """Override to automatically sync status on post"""

        result = super().action_post()
        for move in self:
            if move.caram_transaction_id:
                try:
                    move._send_caram_status_update('confirm')
                    transaction = self.env['loyalty.history'].sudo().search(
                        [('order_id', '=', move.id)],
                          limit=1
                        )

                    _logger.info(
                    "NOT FOUND | No loyalty.history for move %s",
                    move.id
                )
                    if not transaction:
                        continue

                    transaction.write({'status': 'posted'})

                    # -------------------- Update Card Points --------------------
                    card = transaction.card_id
                    if card:
                        new_balance = card.points + transaction.issued
                        card.write({'points': new_balance})
                        _logger.info(
                    "NOT FOUND | No loyalty.history for move %s",
                    new_balance
                )
                except Exception as e:
                    _logger.warning(f"Failed to sync CarAm status on post: {str(e)}")
        return result

    def action_cancel(self):
        """Override to automatically sync status on cancel"""
        result = super().action_cancel()
        for move in self:
            if move.caram_transaction_id:
                try:
                    move._send_caram_status_update('decline')
                except Exception as e:
                    _logger.warning(f"Failed to sync CarAm status on cancel: {str(e)}")
        return result