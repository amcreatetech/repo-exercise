# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo import Command


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    caram_subscription_id = fields.Char(
        string='CarAm Subscription ID',
        help='External subscription reference from CarAm system'
    )

    @api.model
    def create_subscription_with_invoice(self, partner_id, caram_subscription_id, 
                                         subscription_type, price, start_date, 
                                         end_date, company_id):
        """Create subscription, invoice, and pay from wallet balance"""
        
        # 1. Get subscription plan
        plan = self._get_subscription_plan(subscription_type)
        if not plan:
            return {'error': 'Subscription plan not configured', 'status_code': 404}

        # 2. Get or create product
        product = self._get_subscription_product(subscription_type, price)
        if not product:
            return {'error': 'Subscription product not found', 'status_code': 404}

        # 3. Get partner and wallet
        partner = self.env['res.partner'].browse(partner_id)
        wallet = self.env['loyalty.card'].sudo().search([
            ('partner_id', '=', partner_id)
        ], limit=1)
        
        if not wallet:
            return {'error': 'Wallet not found for this partner', 'status_code': 404}

        # 4. Check wallet balance
        print(f"Wallet balance: {wallet.points}")
        print(f"Price: {price}")
        wallet_balance = wallet.points
        print(f"Wallet balance: {wallet_balance}")
        if price > wallet_balance:
            return {'error': 'Insufficient balance to pay invoice', 'status_code': 402}

        # 5. Create subscription (sale.order with plan_id)
        subscription = self.sudo().create({
            'partner_id': partner_id,
            'plan_id': plan.id,
            'date_order': start_date,
            'start_date': start_date,
            'end_date': end_date,
            'caram_subscription_id': caram_subscription_id,
            'company_id': company_id,
            'order_line': [Command.create({
                'product_id': product.id,
                'name': subscription_type,
                'qty_delivered': 1,
                'product_uom_qty': 1,
                'price_unit': price,
            })],
        })
        
        # Confirm the order
        subscription.action_confirm()

        # 6. Create invoice from sale order
        # Create invoice from the confirmed sale order
        invoices = subscription._create_invoices()
        if not invoices:
            return {'error': 'Failed to create invoice', 'status_code': 500}
        
        invoice = invoices[0] if len(invoices) > 0 else invoices
        if not invoice:
            return {'error': 'Failed to create invoice', 'status_code': 500}
        # --- FIX START: Explicitly set the invoice date to the start_date ---
        invoice.sudo().write({
          'invoice_date': start_date,
          })
        # --- FIX END ---
        
        # Set journal if configured (for subscription invoices)
        journal = self.env['account.journal'].sudo().search([
            ('company_id', '=', company_id),
            ('type', '=', 'sale'),
            ('is_used_for_subscriptions', '=', True)
        ], limit=1)
        if journal:
            invoice.sudo().write({'journal_id': journal.id})

        invoice.sudo().action_post()

        # 7. Pay invoice from wallet
        payment_result = self._pay_invoice_from_wallet(invoice, wallet, price, subscription)
        if payment_result.get('error'):
            return payment_result

        # 8. Return response data
        return {
            'subscription_id': subscription.id,
            'odoo_partner_id': partner_id,
            'caram_subscription_id': caram_subscription_id,
            'subscription_type': subscription_type,
            'invoice_id': invoice.id,
            'invoice_number': invoice.name or invoice.id,
            'invoice_state': invoice.state,
            'payment_status': invoice.status_in_payment,
            'start_date': start_date,
            'end_date': end_date,
        }

    def _get_subscription_plan(self, subscription_type):
        """Get subscription plan based on type"""
        # Validate subscription_type
        valid_types = ['private', 'pinky', 'vip', 'van', 'taxi', 'other']
        if subscription_type not in valid_types:
            return False
        
        # Search for plan using the caram_subscription_type field
        plan = self.env['sale.subscription.plan'].sudo().search([
            ('caram_subscription_type', '=', subscription_type)
        ], limit=1)
        
        if plan:
            return plan
        
        return False
        
    def _get_subscription_product(self, subscription_type, price):
        """Get or create subscription product"""
        product = self.env['product.product'].sudo().search([
            ('recurring_invoice', '=', True),
            ('type', '=', 'service')
        ], limit=1)
        
        if not product:
            # Create product if not exists
            product = self.env['product.product'].sudo().create({
                'name': f'Subscription - {subscription_type.title()}',
                'default_code': f'SUBS_{subscription_type.upper()}',
                'type': 'service',
                'recurring_invoice': True,
                'invoice_policy': 'order',  # Invoice based on ordered quantities
                'list_price': price,
            })
        
        return product


    def _pay_invoice_from_wallet(self, invoice, wallet, amount, subscription):
        """Pay invoice using wallet balance"""
        # Find available payments
        payments = self.env['account.payment'].sudo().search([
            ('partner_id', '=', invoice.partner_id.id),
            ('state', '=', 'posted'),
            ('payment_type', '=', 'inbound'),
            ('is_reconciled', '=', False),
        ])

        # Get invoice lines to reconcile
        invoice_lines = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
        )
        payment_lines = payments.mapped('move_id.line_ids').filtered(
            lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
        )

        # Reconcile if possible
        lines_to_reconcile = invoice_lines + payment_lines
        if lines_to_reconcile:
            try:
                lines_to_reconcile.sudo().reconcile()
            except Exception as e:
                return {'error': f'Failed to reconcile payment: {str(e)}', 'status_code': 500}

        # Create loyalty history record
        self.env['loyalty.history'].sudo().create({
            'card_id': wallet.id,
            'description': 'wallet_withdraw_transaction_for_subscription',
            'used': amount,
            'order_model': 'sale.order',
            'order_id': subscription.id,
            'status': 'posted',
        })

        # Update wallet points
        new_balance = wallet.points - amount
        wallet.sudo().write({'points': new_balance})

        return {'success': True}

