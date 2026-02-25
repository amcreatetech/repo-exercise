# -*- coding: utf-8 -*-

from odoo import http
from odoo.http import request
from odoo.exceptions import UserError, ValidationError
import json



class SubscriptionController(http.Controller):
    """Controller for subscription management APIs"""

    def _authenticate(self):
        """Validate Bearer Token and return the user"""

        auth = request.httprequest.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise UserError("Missing or invalid Authorization header")
        
        token = auth.split(" ")[1]
        user_id = request.env["res.users.apikeys"]._check_credentials(
            scope="api", key=token
        )
        if not user_id:
            raise UserError("Invalid API token")
        
        return request.env["res.users"].sudo().browse(int(user_id))

    def _get_env(self, user):
        """Get environment with company context"""
        company = user.company_id
        return request.env(
            user=user,
            context=dict(
                request.env.context,
                allowed_company_ids=[company.id],
                company_id=company.id,
            )
        )

    @http.route("/api/create_subscription", type="http", auth="none", methods=["POST"], csrf=False)
    def create_subscription(self, **kw):
        """Create subscription, invoice, and pay from wallet"""
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))

            # Authentication
            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

            # Extract and validate data
            odoo_partner_id = payload.get('odoo_partner_id')
            if not odoo_partner_id:
                return request.make_json_response({'error': 'odoo_partner_id is required'}, status=400)

            caram_subscription_id = payload.get('caram_subscription_id')
            if not caram_subscription_id:
                return request.make_json_response({'error': 'caram_subscription_id is required'}, status=400)

            subscription_type = payload.get('subscription_type')
            if not subscription_type:
                return request.make_json_response({'error': 'subscription_type is required'}, status=400)

            price = payload.get('Price')
            if price is None:
                return request.make_json_response({'error': 'Price is required'}, status=400)
            try:
                price = float(price)
            except (ValueError, TypeError):
                return request.make_json_response({'error': 'Price must be a valid number'}, status=400)
            if price <= 0:
                return request.make_json_response({'error': 'Price must be greater than 0'}, status=400)

            start_date = payload.get('start_date')
            if not start_date:
                return request.make_json_response({'error': 'start_date is required'}, status=400)

            end_date = payload.get('end_date')
            if not end_date:
                return request.make_json_response({'error': 'end_date is required'}, status=400)

            if subscription_type not in ['private', 'pinky', 'vip', 'van', 'taxi', 'other']:
                return request.make_json_response({'error': 'Invalid subscription_type'}, status=400)

            # Check for existing subscription
            existing = env['sale.order'].sudo().search([
                ('caram_subscription_id', '=', caram_subscription_id),
                ('plan_id', '!=', False),  # Only check orders with plan_id (subscriptions)
            ], limit=1)
            # if existing:
            #     return request.make_json_response({'error': 'Subscription with this caram_subscription_id already exists'}, status=409)

            # # Get partner
            partner = env['res.partner'].sudo().browse(odoo_partner_id)
            if not partner.exists():
                return request.make_json_response({'error': 'Partner not found'}, status=404)

            subscription_model = env['sale.order']
            result = subscription_model.create_subscription_with_invoice(
                partner_id=partner.id,
                caram_subscription_id=caram_subscription_id,
                subscription_type=subscription_type,
                price=price,
                start_date=start_date,
                end_date=end_date,
                company_id=company_id,
            )

            if result.get('error'):
                return request.make_json_response(result, status=result.get('status_code', 500))

            return request.make_json_response({
                'status': 'success',
                'message': 'Subscription created and invoiced successfully',
                'data': result
            }, status=201)

        except json.JSONDecodeError:
            return request.make_json_response({'error': 'Invalid JSON format'}, status=400)
        except UserError as e:
            return request.make_json_response({'error': str(e)}, status=400)
        except ValidationError as e:
            return request.make_json_response({'error': str(e)}, status=400)
        except Exception as e:
            return request.make_json_response({'error': f'Failed to create subscription: {str(e)}'}, status=500)

