import traceback

from odoo import fields, http, _
from odoo.http import request
from odoo.exceptions import UserError
import json
import logging
_logger = logging.getLogger(__name__)

class ContactRegistrationController(http.Controller):
    
    def _get_env(self, user, company_id=None):
        """Build env forced to API/user company (company-dependent accounting)."""
        if company_id:
            company = request.env["res.company"].sudo().browse(int(company_id))
            if not company.exists():
                raise UserError("Invalid company_id")
            if company not in user.company_ids:
                raise UserError("User has no access to this company")
        else:
            company = user.company_id
        return request.env(
            user=user,
            context=dict(
                request.env.context,
                allowed_company_ids=[company.id],
                company_id=company.id,
            ),
        )

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

    def _get_wallet_accounts(self, env, company_id, contact_type, coupon_value=0):
        """Get and validate wallet accounts for a given contact type"""
        company = env["res.company"].sudo().browse(company_id)

        # Get accounts from company configuration
        if coupon_value>0:
          bank_account = company.caram_bouns_account_id
        else:
          bank_account = company.caram_bank_account_id

        if contact_type == "rider":
            liability_account = company.caram_rider_wallets_account_id
        elif contact_type == "driver":
            liability_account = company.caram_driver_wallet_account_id
        else:
            liability_account = False

        # Validate accounts exist
        if not bank_account:
            return None, None, request.make_json_response({"error": "Bank account not configured in company settings"}, status=500)
        if not liability_account:
            return None, None, request.make_json_response({"error": f"{contact_type.capitalize()} wallet account not configured in company settings"}, status=500)

        # Validate account companies
        for account in (bank_account, liability_account):
            if account:
                if not account.exists():
                    return None, None, request.make_json_response({"error": "Account not found or invalid"}, status=500)
                # Check if account is accessible by the company
                if not account.company_ids or company_id not in account.company_ids.ids:
                    return None, None, request.make_json_response({"error": "Bank account company mismatch"}, status=500)

        return bank_account, liability_account, None


    def create_driver_coupon_credit_note(
        self,
        env,
        company_id,
        partner,
        amount,
        description,
        product=None,
        accounting_date=None,
        note_from_api=False,
        api_payload=False,
    ):
        """Create & post a customer credit note.

        If a product is provided, it will be used directly (e.g. compensation product);
        otherwise, the legacy 'is_coupon' product will be searched.
        """
        product_coupon = product
        if not product_coupon:
            product_coupon = env['product.product'].sudo().with_company(company_id).search(
                [('is_coupon', '=', True)],
                limit=1,
            )
        if not product_coupon:
            return False

        expense_account = (
            product_coupon.property_account_expense_id
            or product_coupon.categ_id.property_account_expense_categ_id
            
        )
        if not expense_account:
            expense_account = env['account.account'].sudo().with_company(company_id).search(
                [('company_id', '=', company_id), ('account_type', 'in', ('expense'))],
                limit=1,
            )
        if not expense_account:
            return False

        doc_date = accounting_date or fields.Date.context_today(env.user)
        credit_note = env['account.move'].sudo().with_company(company_id).create({
            'partner_id': partner.id,
            'move_type': 'out_refund',
            'invoice_date': doc_date,
            'date': doc_date,
            'invoice_date_due': doc_date,
            'invoice_line_ids': [(0, 0, {
                'product_id': product_coupon.id,
                'account_id': expense_account.id,
                'name': description,
                'quantity': 1,
                'price_unit': amount,
            })],
            'is_from_api': True,
            'note_from_api': note_from_api or False,
            'api_payload': api_payload or False,
        })
        
        # Post the credit note to make it effective
        credit_note.action_post()
        return credit_note
    
    @http.route("/api/register_contact", type="http", auth="none", methods=["POST"], csrf=False)
    def register_contact(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

            # -------------------- Extract Data --------------------
            sub_id = payload.get("sub_id")
            name = payload.get("name")
            email = payload.get("email")
            mobile = payload.get("mobile")
            city = payload.get("city")
            gender = payload.get("gender")
            contact_type = payload.get("contact_type")
            coupon_value = float(payload.get("coupon_value", 0.0))
            billing_type = payload.get("billing_type") # subscription,commission
            accounting_date = payload.get("date") or False
            note_from_api = payload.get("note_from_api") or False
            api_payload = payload or False

            # -------------------- Required Fields --------------------
            if not sub_id:
                return request.make_json_response({"error": "sub_id is required"}, status=400)

            if not name:
                return request.make_json_response({"error": "name is required"}, status=400)

            # -------------------- Validate optional fields --------------------
            if gender and gender not in ["male", "female"]:
                return request.make_json_response({"error": "Invalid gender"}, status=400)

            if contact_type and contact_type not in ["driver", "rider"]:
                return request.make_json_response({"error": "Invalid contact_type"}, status=400)
            
            if billing_type and billing_type not in ["commission", "subscription"]:
                return request.make_json_response({"error": "Invalid Billing_type"}, status=400)

            # -------------------- Check Existing Contact --------------------
            domain = []
            if sub_id:
                domain.append(('sub_id', '=', sub_id))
            if mobile:
                domain.append(('mobile', '=', mobile))

            
            existing = env["res.partner"].sudo().search(domain, limit=1)

            if existing:
                return request.make_json_response({"error": "Contact with this email or mobile already exists"}, status=409)
            else:
                # -------------------- Create Contact --------------------
                partner_vals = {
                    "name": name,
                    "sub_id": sub_id,
                    "email": email,
                    "mobile": mobile,
                    "phone": mobile,
                    "city": city,
                    "gender": gender,
                    "contact_type": contact_type,
                    "billing_type": billing_type,
                    
                    "customer_rank": 1,
                    "type": "contact",
                    "user_id": user.id,
                }

                partner = env["res.partner"].sudo().create(partner_vals)

            # -------------------- Create Wallet --------------------
            program = env["loyalty.program"].sudo().search([
                ("program_type", "=", "ewallet")
            ], limit=1)

            if not program:
                return request.make_json_response({"error": "e-Wallet program not found"}, status=500)

            card = env["loyalty.card"].sudo().create({"program_id": program.id, "partner_id": partner.id})
            wallet_balance = 0.0
            description = "Welcome Coupon - Service Credit"

            if coupon_value > 0:
                credit_note = self.create_driver_coupon_credit_note(
                    env,
                    company_id,
                    partner,
                    coupon_value,
                    description,
                    accounting_date=accounting_date,
                    note_from_api=note_from_api,
                    api_payload=api_payload,
                )
                if credit_note:
                    # -------------------- Create Loyalty History --------------------
                    env["loyalty.history"].sudo().create({
                        "card_id": card.id,
                        "description": "Welcome Coupon - Service Credit",
                        "issued": coupon_value,
                        "order_model": "account.move",
                        "order_id": credit_note.id,
                        "status": "posted",
                    })
                
                # -------------------- Update Card Points --------------------
                card.sudo().write({"points": coupon_value})
                wallet_balance = coupon_value

            # -------------------- Response --------------------
            data = {
                "odoo_partner_id": partner.id,
                "name": partner.name,
                "email": partner.email or "",
                "mobile": partner.mobile or "",
                "wallet_id": card.id,
                "wallet_balance": wallet_balance,
            }

            return request.make_json_response({"status": "success", "message": "Contact registered successfully", "data": data}, status=201)

        except Exception as e:
            return request.make_json_response({"error": str(e)}, status=500)


    @http.route('/api/delete_contact', type='http', auth='none', methods=['POST'], csrf=False)
    def delete_contact(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode('utf-8'))

            # -------------------- Auth --------------------
            user = self._authenticate()
            env = request.env(user=user)

            # -------------------- Input --------------------
            partner_id = payload.get('odoo_partner_id')
            if not partner_id:
                return json.dumps({
                    "status": "error",
                    "message": "odoo_partner_id is required"
                })

            partner = env['res.partner'].browse(int(partner_id))

            # -------------------- Validation --------------------
            if not partner.exists():
                return json.dumps({
                    "status": "error",
                    "message": "Contact not found"
                })

            # Optional: prevent deleting companies or important contacts
            if partner.is_company:
                return json.dumps({
                     "status": "error",
                     "message": "Cannot delete company contacts"
                 })

            # -------------------- Delete --------------------
            partner.unlink()

            return request.make_json_response({"status": "success", "message": "Contact deleted successfully"}, status=200)

        except Exception as e:
            return request.make_json_response({"error": f"Failed to update contact: {str(e)}"}, status=500)
        
        
    @http.route("/api/update_contact", type="http", auth="none", methods=["PUT"], csrf=False)
    def update_contact(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            print("update_contact payload:", payload)

            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

            # ------------------------------------------------------------------
            # Extract data
            # ------------------------------------------------------------------
            partner_id = payload.get("odoo_partner_id")
            email = payload.get("email")
            mobile = payload.get("mobile")
            name = payload.get("name")
            city = payload.get("city")
            gender = payload.get("gender")
            contact_type = payload.get("contact_type")
            billing_type = payload.get("billing_type") # subscription,commission

            # ------------------------------------------------------------------
            # Validate required identifier
            # ------------------------------------------------------------------
            if not partner_id and not email and not mobile:
                return request.make_json_response({"error": "Odoo_partner_id, email or mobile is required"}, status=400)

            # ------------------------------------------------------------------
            # Search for the contact
            # ------------------------------------------------------------------
            domain = [('company_id', '=', company_id)]
            if partner_id:
                domain.append(('id', '=', partner_id))
            elif email:
                domain.append(('email', '=', email))
            elif mobile:
                domain.append(('mobile', '=', mobile))

            partner = env['res.partner'].sudo().search(domain, limit=1)

            if not partner:
                return request.make_json_response({"error": "No contact found with this email or mobile"}, status=404)

            # ------------------------------------------------------------------
            # Update fields if provided
            # ------------------------------------------------------------------
            update_vals = {}
            if name:
                update_vals['name'] = name
            if email:
                update_vals['email'] = email
            if mobile:
                update_vals['mobile'] = mobile
                update_vals['phone'] = mobile
            if city:
                update_vals['city'] = city
            if gender:
                if gender not in ['male', 'female']:
                    return request.make_json_response({"error": "Invalid gender"}, status=400)
                update_vals['gender'] = gender
            if contact_type:
                if contact_type not in ['driver', 'rider']:
                    return request.make_json_response({"error": "Invalid contact_type"}, status=400)
                update_vals['contact_type'] = contact_type
            
            if billing_type:
                if billing_type not in ['commission', 'subscription']:
                    return request.make_json_response({"error": "Invalid billing_type"}, status=400)
                update_vals['billing_type'] = billing_type

            if update_vals:
                partner.sudo().write(update_vals)

            # ------------------------------------------------------------------
            # Response
            # ------------------------------------------------------------------
            data = {
                "partner_id": partner.id,
                "name": partner.name,
                "email": partner.email or "",
                "mobile": partner.mobile or "",
                "city": partner.city or "",
                "gender": partner.gender or "",
                "contact_type": partner.contact_type or "",
                "billing_type": partner.billing_type or ""
            }

            return request.make_json_response({"status": "success", "message": "Contact updated successfully", "data": data}, status=200)

        except Exception as e:
            return request.make_json_response({"error": f"Failed to update contact: {str(e)}"}, status=500)


    
    @http.route("/api/get_balance", type="http", auth="none", methods=["POST"], csrf=False)
    def get_wallet_balance(self):
        """Return wallet balances from Loyalty Card."""
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))

            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

        # -------------------------
        # Input Handling
        # -------------------------
            raw_partner_id = payload.get("odoo_partner_id")
            type_raw = payload.get("type") or payload.get("Type")

            partner_pk = None
            if raw_partner_id not in (None, "", False):
                try:
                    partner_pk = int(raw_partner_id)
                except (TypeError, ValueError):
                    return request.make_json_response(
                    {"error": "Invalid odoo_partner_id"}, status=400
                )

            contact_kind = None
            if type_raw not in (None, "", False):
                contact_kind = str(type_raw).strip().lower()
                if contact_kind not in ("rider", "driver"):
                    return request.make_json_response(
                    {"error": 'Invalid type; expected "rider" or "driver"'},
                    status=400,
                )

        # -------------------------
        # Case 1: Specific Partner
        # -------------------------
            if partner_pk:
                partner = env["res.partner"].sudo().browse(partner_pk)

                if not partner.exists() or partner.company_id.id != company_id:
                    return request.make_json_response(
                    {"error": "Partner not found or does not belong to this company"},
                    status=404,
                )

                card = env["loyalty.card"].sudo().search(
                [
                    ("partner_id", "=", partner.id)
                ],
                limit=1,
            )

                if not card:
                    return request.make_json_response(
                    {"error": "Wallet not found for this partner"}, status=404
                )

                return request.make_json_response(
                {
                    "status": 200,
                    "type": "single_user",
                    "data": {
                        "user_id": partner.id,
                        "name": partner.name,
                        "balance": float(card.points or 0.0),
                    },
                },
                status=200,
            )

        # -------------------------
        # Case 2: List (filtered or all)
        # -------------------------
            domain = [("company_id", "=", company_id)]

            if contact_kind:
                domain.append(("partner_id.contact_type", "=", contact_kind))

            cards = env["loyalty.card"].sudo().search(domain, order="partner_id")

            data = []
            for card in cards:
                partner = card.partner_id
                data.append({
                "user_id": partner.id,
                "name": partner.name,
                "type": partner.contact_type or "",
                "balance": float(card.points or 0.0),
            })

            return request.make_json_response(
            {
                "status": 200,
                "count": len(data),
                "data": data,
            },
            status=200,
        )

        except Exception as e:
            return request.make_json_response(
            {"error": str(e)},
            status=500,
        )    
    
    def old_get_balance(self, **kw):
        """Return wallet balances from Loyality Card."""
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

            raw_partner_id = payload.get("odoo_partner_id")
            type_raw = payload.get("type") or payload.get("Type")
            contact_kind = str(type_raw).strip().lower() if type_raw is not None and type_raw != "" else None

            if raw_partner_id is not None:
                try:
                    partner_pk = int(raw_partner_id)
                except (TypeError, ValueError):
                    return request.make_json_response({"error": "Invalid odoo_partner_id"}, status=400)

                partner = env["res.partner"].sudo().browse(partner_pk)
                if not partner.exists() or partner.company_id.id != company_id:
                    return request.make_json_response(
                        {"error": "Partner not found or does not belong to this company"},
                        status=404,
                    )

                card = env["loyalty.card"].sudo().search(
                    [("partner_id", "=", partner.id)],
                    limit=1,
                )
                if not card:
                    return request.make_json_response({"error": "Wallet not found for this partner"}, status=404)

                body = {
                    "status": 200,
                    "type": "single_user",
                    "data": {
                        "user_id": partner.id,
                        "name": partner.name,
                        "balance": float(card.points or 0.0),
                    },
                }
                return request.make_json_response(body, status=200)

            if contact_kind:
                if contact_kind not in ("rider", "driver"):
                    return request.make_json_response(
                        {"error": 'Invalid type; expected "rider" or "driver"'},
                        status=400,
                    )
                domain = [
                    ("company_id", "=", company_id),
                    ("partner_id.contact_type", "=", contact_kind),
                ]
            else:
                domain = [("company_id", "=", company_id)]

            cards = env["loyalty.card"].sudo().search(domain, order="partner_id")
            rows = []
            for card in cards:
                partner = card.partner_id
                rows.append({
                    "user_id": partner.id,
                    "name": partner.name,
                    "type": partner.contact_type or "",
                    "balance": float(card.points or 0.0),
                })

            return request.make_json_response(
                {"status": 200, "count": len(rows), "data": rows},
                status=200,
            )

        except Exception as e:
            return request.make_json_response({"error": str(e)}, status=500)

    @http.route("/api/add_wallet_transaction", type="http", auth="none", methods=["POST"], csrf=False)
    def add_wallet_transaction(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

            # -------------------- Extract Data --------------------
            odoo_partner_id = payload.get("odoo_partner_id")
            transaction_id = payload.get("transaction_id")
            payment_method_type = payload.get("payment_method_type")
            salesperson_id = payload.get("salesperson_id")
            transaction_type = payload.get("transaction_type")
            amount = payload.get("amount") 
            reference = payload.get("reference")
            bank = payload.get("bank")
            image_url = payload.get("image_url") or payload.get("Image_url")
            note = payload.get("note")
            account_number = payload.get("account_number")
            accounting_date = payload.get("date") or False
            note_from_api = payload.get("note_from_api") or False
            api_payload = payload

            # -------------------- Validate required fields --------------------
            if not odoo_partner_id:
                return request.make_json_response({"error": "odoo_partner_id is required"}, status=400)
            if not transaction_id:
                return request.make_json_response({"error": "transaction_id is required"}, status=400)
            if not transaction_type:
                return request.make_json_response({"error": "transaction_type is required"}, status=400)
            if transaction_type not in ["direct", "bank_transfer"]:
                return request.make_json_response({"error": "Invalid transaction_type"}, status=400)
            if not amount or amount <= 0:
                return request.make_json_response({"error": "amount is required and must be greater than 0"}, status=400)
            if payment_method_type == "salesperson" and not salesperson_id:
                return request.make_json_response(
                    {"error": "salesperson_id is required for payment_method_type = 'salesperson'"},
                    status=400,
                )

            # -------------------- Find Partner --------------------
            partner = env['res.partner'].sudo().browse(odoo_partner_id)
            if not partner.exists() or partner.company_id.id != company_id:
                return request.make_json_response({"error": "Partner not found or does not belong to this company"}, status=404)

            # -------------------- Find Wallet --------------------
            wallet = env['loyalty.card'].sudo().search([('partner_id', '=', partner.id)], limit=1)
            if not wallet:
                return request.make_json_response({"error": "Wallet not found for this partner"}, status=404)


            contact_type = partner.contact_type
            bank_account, liability_account, error_response = self._get_wallet_accounts(env, company_id, contact_type)
            if error_response:
                return error_response

            move = None
            journal_transaction_id = None
            move_credit = None
            
            if bank_account and liability_account:
                ref = note
                should_post = (transaction_type == "direct")
                state = 'posted' if should_post else 'draft'
              
                if payment_method_type == 'points':
                    move_credit = wallet.create_points_credit_note(
                        env,
                        company_id,
                        partner,
                        amount,
                        accounting_date=accounting_date,
                        note_from_api=note_from_api,
                        api_payload=api_payload,
                    )
                    
                elif payment_method_type == 'salesperson':
                    salesperson = env['res.partner'].sudo().browse(salesperson_id)
                    if not salesperson.exists():
                        return request.make_json_response(
                            {"error": "Salesperson not found or does not belong to this company"},
                            status=404,
                        )

                    wallet_receivable = partner.with_company(company_id).property_account_receivable_id
                    if not wallet_receivable:
                        return request.make_json_response(
                            {"error": "Wallet partner has no receivable account configured"},
                            status=500,
                        )

                    journal = env['account.journal'].sudo().with_company(company_id).search(
                        [("wallet_type_id", "=", payment_method_type), '|', ('company_id', '=', company_id), ('company_id', 'parent_of', company_id)],
                        limit=1,
                    )

                    if not journal:
                        return request.make_json_response(
                            {"error": "No general journal found to post wallet salesperson entries"},
                            status=500,
                        )

                    move_vals = {
                        'move_type': 'entry',
                        'journal_id': journal.id,
                        'date': accounting_date,
                        'ref': f'Wallet top-up via Salesperson {salesperson.display_name}',
                        'is_from_api': True,
                        'note_from_api': note_from_api or False,
                        'api_payload': api_payload or False,
                        'line_ids': [
                            (0, 0, {
                                'name': ref or 'Wallet top-up via Salesperson',
                                'partner_id': salesperson.id,
                                'account_id': salesperson.property_account_receivable_id.id,
                                'debit': amount,
                                'credit': 0.0,
                            }),
                            (0, 0, {
                                'name': ref or 'Wallet top-up via Salesperson',
                                'partner_id': partner.id,
                                'account_id': wallet_receivable.id,
                                'debit': 0.0,
                                'credit': amount,
                            }),
                        ],
                    }

                    move = env['account.move'].sudo().with_company(company_id).create(move_vals)
                    if should_post:
                        move.action_post()
                    journal_transaction_id = transaction_id
                else:
                    move, error = wallet._create_payment(
                        partner,
                        amount,
                        payment_method_type,
                        ref,
                        should_post=should_post,
                        transaction_id=transaction_id,
                        image_url=image_url,
                        bank=bank,
                        account_number=account_number,
                        accounting_date=accounting_date,
                        note_from_api=note_from_api,
                        api_payload=api_payload,
                        company_id=company_id,
                    )
                    if move:
                        journal_transaction_id = move.caram_transaction_id
                    if error:
                        return request.make_json_response({"error": str(error)}, status=500)
                    if not move:
                        return request.make_json_response({"error": "Failed to create payment"}, status=500)

            # -------------------- Create Wallet Transaction --------------------
                transaction_vals = {
                "card_id": wallet.id,
                "description": note or "",
                "issued": amount,
                "deposit_method": transaction_type,
                "reference": reference or "",
                "bank": bank or "",
                "account_number": account_number or "",
                "status": state,
                
            }
            
                if move:
                    transaction_vals.update({
                    "order_model": move._name,
                    "order_id": move.id,
                })
                elif move_credit:
                    transaction_vals.update({
                    "order_model": "account.move",
                    "order_id": move_credit.id,
                })

                else:
                    transaction_vals.update({
                    "order_model": "res.partner",
                    "order_id": partner.id,
                })

                transaction = env['loyalty.history'].sudo().create(transaction_vals)
            

                # Calculate balance: sum of issued minus sum of used for posted records
                posted_history = env['loyalty.history'].sudo().search([
                ('card_id', '=', wallet.id), 
                ('status', '=', 'posted')
            ])
                total_issued = sum(posted_history.mapped('issued') or [0.0])
                total_used = sum(posted_history.mapped('used') or [0.0])
                total_balance = total_issued - total_used

            # -------------------- Update Card Points --------------------
                wallet_balance = total_balance
                wallet.sudo().write({"points": wallet_balance})

            # -------------------- Response --------------------
                data = {
                "transaction_id": transaction.id,
                "journal_entry_id": move.id if move else move_credit.id,
                "journal_transaction_id": journal_transaction_id,
                "partner_id": partner.id,
                "wallet_id": wallet.id,
                "amount": amount,
                "deposit_method": transaction_type,
                "state": state,
                "balance_after": total_balance,
            }

                return request.make_json_response({"status": "success", "message": "Wallet transaction created successfully", "data": data}, status=201)

        except Exception as e:
            return request.make_json_response({"error": f"Failed to create wallet transaction: {str(e)}"}, status=500)

    @http.route("/api/wallet_withdraw", type="http", auth="none", methods=["POST"], csrf=False)
    def wallet_withdraw(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))

            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id

            # -------------------- Extract data --------------------
            partner_id = payload.get("odoo_partner_id")
            amount = float(payload.get("amount", 0))
            transaction_id = payload.get("transaction_id")
            transaction_type = payload.get("transaction_type")
            payment_method_type = payload.get("payment_method_type")
            bank = payload.get("bank")
            account_number = payload.get("account_number")
            note = payload.get("note") or ""
            accounting_date = payload.get("date") or False
            note_from_api = payload.get("note_from_api") or False
            api_payload = payload

            # -------------------- Validate required fields --------------------
            if not transaction_type:
                return request.make_json_response({"error": "transaction_type is required"}, status=400)
            if transaction_type not in ["direct", "bank_transfer"]:
                return request.make_json_response({"error": "Invalid transaction_type"}, status=400)
            if not partner_id:
                return request.make_json_response({"error": "odoo_partner_id is required"}, status=400)
            if not amount or amount <= 0:
                return request.make_json_response({"error": "amount is required and must be greater than 0"}, status=400)
            if not transaction_id:
                return request.make_json_response({"error": "transaction_id is required"}, status=400)

            partner = env['res.partner'].sudo().browse(partner_id)
            if not partner:
                return request.make_json_response({"error": "Partner not found"}, status=404)

            wallet = env['loyalty.card'].sudo().search([('partner_id', '=', partner.id)], limit=1)
            if not wallet:
                return request.make_json_response({"error": "No wallet found for this partner"}, status=404)

            net_amount = amount
            
            # Calculate balance: sum of issued minus sum of used for posted records
            posted_history = env['loyalty.history'].sudo().search([
                ('card_id', '=', wallet.id), 
                ('status', '=', 'posted')
            ])
            total_issued = sum(posted_history.mapped('issued') or [0.0])
            total_used = sum(posted_history.mapped('used') or [0.0])
            total_balance = total_issued - total_used
            
            # TODO 
            # if net_amount > total_balance:
            #     return request.make_json_response({"error": "Insufficient wallet balance"}, status=409)
            
            # -------------------- Create Journal Entry --------------------
            contact_type = partner.contact_type
            bank_account, liability_account, error_response = self._get_wallet_accounts(env, company_id, contact_type, coupon_value=0)
            if error_response:
                return error_response

            move = None
            if bank_account and liability_account:
                ref = note
                should_post = (transaction_type == "direct")
                state = 'posted' if should_post else 'draft'
                payment_method_type = payment_method_type
                image_url = ''
              
                move, error = wallet._create_payment(
                    partner,
                    -amount,
                    payment_method_type,
                    ref,
                    should_post=should_post,
                    transaction_id=transaction_id,
                    image_url=image_url,
                    bank=bank,
                    account_number=account_number,
                    accounting_date=accounting_date,
                    note_from_api=note_from_api,
                    api_payload=api_payload,
                    company_id = company_id
                )
                if error:
                    return request.make_json_response({"error": str(error)}, status=500)
            
            transaction_vals = {
                "card_id": wallet.id,
                "description": f"Wallet withdraw. {note}",
                "issued": -net_amount,
                "status": state,
            }
            
            # Link to journal entry if created, otherwise link to partner
            if move:
                transaction_vals.update({
                    "order_model": "account.payment",
                    "order_id": move.id,
                })
            else:
                transaction_vals.update({
                    "order_model": "res.partner",
                    "order_id": partner.id,
                })
            
            transaction = env['loyalty.history'].sudo().create(transaction_vals)
            balance_after = total_balance - net_amount
            
            # -------------------- Update Card Points --------------------
            wallet.sudo().write({"points": balance_after})

            data = {
                "transaction_id": transaction.id if transaction else 0,
                "net_amount": net_amount,
                "balance_after": balance_after
            }

            return request.make_json_response({"status": "success", "message": "Withdrawal transaction created successfully", "data": data}, status=201)

        except Exception as e:
            return request.make_json_response({"error": f"Failed to create withdrawal transaction: {str(e)}"}, status=500)


    @http.route("/api/compensation", type="http", auth="none", methods=["POST"], csrf=False)
    def wallet_compensation(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))

            user = self._authenticate()
            env = self._get_env(user)
            company_id = payload.get("company_id")

            odoo_partner_id = payload.get("odoo_partner_id")
            comp_type = (payload.get("type") or "").strip().lower()
            amount = payload.get("amount")
            note = payload.get("note") or ""
            accounting_date = payload.get("date") or False
            note_from_api = payload.get("note_from_api") or False
            api_payload = payload

            # -------------------- Validate input --------------------
            if amount is None or amount == 0:
                return request.make_json_response({"error": "amount is required"}, status=400)

            if not odoo_partner_id:
                return request.make_json_response(
                    {"status": 400, "message": "odoo_partner_id is required"}, status=400
                )

            allowed_types = ["bonus", "driver_coupon", "rider_coupon", "discount", "fees"]
            if comp_type not in allowed_types:
                return request.make_json_response(
                    {"status": 400, "message": f"Invalid type (must be one of {allowed_types})"},
                    status=400,
                )

            partner = env["res.partner"].sudo().browse(odoo_partner_id)
            if not partner.exists():
                return request.make_json_response(
                    {"status": 404, "message": "Partner not found or does not belong to this company"},
                    status=404,
                )

            # Wallet
            card = (
                env["loyalty.card"]
                .sudo()
                .search(
                    [("partner_id", "=", partner.id)],
                    limit=1,
                )
            )
            if not card:
                return request.make_json_response(
                    {"status": 404, "message": "Wallet not found for this partner"}, status=404
                )

            # -------------------- Resolve product per type --------------------
            # driver_coupon / rider_coupon / fees each need their own product field
            # on res.company. Rename these if your actual fields differ.
            # دور أول على إعداد خاص بالفرع نفسه بالضبط
            config = env["caram.compensation.product.config"].sudo().search(
                [("company_id", "=", company_id), ("type", "=", comp_type)],
                limit=1,
            )
            # إذا مالقيتش، دور على إعداد الأب (الشركة الأم)
            if not config:
                config = env["caram.compensation.product.config"].sudo().search(
                    [("company_id", "parent_of", company_id), ("type", "=", comp_type)],
                    limit=1,
                )
            
            if not config or not config.product_id:
                return request.make_json_response(
                    {
                        "status": 500,
                        "message": f"Compensation product not configured for type '{comp_type}' "
                                f"on company '{company.name}'",
                    },
                    status=500,
                )
            product = config.product_id.with_company(company_id)
            if not product:
                return request.make_json_response(
                    {
                        "status": 500,
                        "message": f"Compensation product not configured for type '{comp_type}' "
                                f"(field '{product_field}' on company settings)",
                    },
                    status=500,
                )
            product = product.with_company(company_id)

            expense_account = (
                product.property_account_expense_id
                or product.categ_id.property_account_expense_categ_id
            )
            if not expense_account:
                return request.make_json_response(
                    {"status": 500, "message": "No expense account configured for compensation product"},
                    status=500,
                )

            is_return = amount < 0
            abs_amount = abs(amount)
            description = f"Wallet compensation ({comp_type}{' - return' if is_return else ''}) {note}"

            # -------------------- Accounting entry --------------------
            if is_return:
                # Negative amount on ANY type -> reversing manual journal entry,
                # using the product/expense account resolved for that type above.
                journal = env["account.journal"].sudo().with_company(company_id).search(
                    [("type", "=", "general"), '|', ('company_id', '=', company_id), ('company_id', 'parent_of', company_id)],
                    limit=1,
                )
                if not journal:
                    raise UserError(_("No journal found to post CarAm wallet transfer entries."))

                wallet_receivable = partner.with_company(company_id).property_account_receivable_id
                if not wallet_receivable:
                    return request.make_json_response(
                        {"error": "Wallet partner has no receivable account configured"},
                        status=500,
                    )

                ref = f"Return ({comp_type}) {partner.name} wallet transfer"
                move_vals = {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": accounting_date,
                    "ref": ref,
                    "is_from_api": True,
                    "note_from_api": note_from_api or False,
                    "api_payload": api_payload or False,
                    "line_ids": [
                        (0, 0, {
                            "name": ref,
                            "partner_id": partner.id,
                            "account_id": wallet_receivable.id,
                            "debit": abs_amount,
                            "credit": 0.0,
                        }),
                        (0, 0, {
                            "name": ref,
                            "partner_id": partner.id,
                            "product_id": product.id,
                            "account_id": expense_account.id,
                            "debit": 0.0,
                            "credit": abs_amount,
                        }),
                    ],
                }

                journal_entry = env["account.move"].sudo().with_company(company_id).create(move_vals)
                journal_entry.action_post()
                move = journal_entry

            elif comp_type in ("bonus", "driver_coupon", "rider_coupon"):
                move = self.create_driver_coupon_credit_note(
                    env,
                    company_id,
                    partner,
                    amount,
                    description,
                    product=product,
                    accounting_date=accounting_date,
                    note_from_api=note_from_api,
                    api_payload=api_payload,
                )
                if not move:
                    return request.make_json_response(
                        {"status": 500, "message": f"Failed to create {comp_type} credit note"},
                        status=500,
                    )

            else:
                # discount / fees -> invoice entry using _create_invoice_from_lines
                invoice_line_vals = {
                    "product_id": product.id,
                    "account_id": expense_account.id,
                    "name": description,
                    "quantity": 1,
                    "price_unit": amount,
                }
                move = card._create_invoice_from_lines(
                    partner,
                    [invoice_line_vals],
                    accounting_date=accounting_date,
                    note_from_api=note_from_api,
                    api_payload=api_payload,
                    company_id= company_id,
                )

            # -------------------- Wallet & loyalty history --------------------
            balance_before = card.caram_get_posted_balance()
            delta = amount if comp_type in ("bonus", "driver_coupon", "rider_coupon") else -amount

            tx_vals = {
                "card_id": card.id,
                "description": description,
                "issued": delta,
                "used": 0.0,
                "status": "posted",
                "order_model": "account.move",
                "order_id": move.id,
            }
            tx = env["loyalty.history"].sudo().create(tx_vals)

            balance_after = card.caram_get_posted_balance()
            card.sudo().write({"points": balance_after})

            response = {
                "status": 200,
                "journal_entry_id": move.id,
                "partner_id": partner.id,
                "wallet_id": card.id,
                "type": comp_type,
                "is_return": is_return,
                "amount": amount,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "loyalty_history_id": tx.id,
                "message": "Compensation applied successfully",
            }
            return request.make_json_response(response, status=200)

        except Exception as e:
            return request.make_json_response(
                {"status": 500, "message": f"Internal server error: {str(e)}"},
                status=500,
            )
    
    @http.route("/api/wallet_clearing", type="http", auth="none", methods=["POST"], csrf=False)
    def wallet_clearing(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id
            company = env["res.company"].sudo().browse(company_id)
            accounting_date = payload.get("date") or False
            note_from_api = payload.get("note_from_api") or False
            api_payload = payload

            rider_id = payload.get("odoo_rider_id")
            driver_id = payload.get("odoo_driver_id")
            amount = payload.get("amount")
            
            if not amount:
                return request.make_json_response({"error": "amount is required"}, status=400)
            
            if not rider_id or not driver_id:
                return request.make_json_response(
                    {
                        "status": 400,
                        "message": "odoo_rider_id and odoo_driver_id are required",
                    },
                    status=400,
                )

            rider = env["res.partner"].sudo().browse(rider_id)
            driver = env["res.partner"].sudo().browse(driver_id)
            if not rider.exists():
                return request.make_json_response(
                    {"status": 404, "message": "Rider not found"}, status=404
                )
            if not driver.exists():
                return request.make_json_response(
                    {"status": 404, "message": "Driver not found"}, status=404
                )

            # Wallet accounts from company configuration
            rider_wallet_account = rider.property_account_receivable_id
            driver_wallet_account = driver.property_account_receivable_id
            if not rider_wallet_account or not driver_wallet_account:
                return request.make_json_response(
                    {
                        "status": 500,
                        "message": "Wallet accounts not configured on company",
                    },
                    status=500,
                )

            journal = company.caram_clearing_journal_id or env[
                "account.journal"
            ].sudo().search(
                [("company_id", "=", company_id), ("type", "=", "general")], limit=1
            )
            if not journal:
                return request.make_json_response(
                    {
                        "status": 500,
                        "message": "No journal found for wallet clearing entries",
                    },
                    status=500,
                )

            base_amount = abs(amount)
            if amount > 0:
                # Rider -> Driver
                debit_partner = rider
                debit_account = rider_wallet_account
                credit_partner = driver
                credit_account = driver_wallet_account
                direction = "rider_to_driver"
            else:
                # Driver -> Rider
                debit_partner = driver
                debit_account = driver_wallet_account
                credit_partner = rider
                credit_account = rider_wallet_account
                direction = "driver_to_rider"

            ref = f"Wallet clearing {direction.replace('_', ' ')}"
            move_vals = {
                "move_type": "entry",
                "journal_id": journal.id,
                "date": accounting_date,
                "ref": ref,
                "is_from_api": True,
                "note_from_api": note_from_api or False,
                "api_payload": api_payload or False,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": ref,
                            "partner_id": debit_partner.id,
                            "account_id": debit_account.id,
                            "debit": base_amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": ref,
                            "partner_id": credit_partner.id,
                            "account_id": credit_account.id,
                            "debit": 0.0,
                            "credit": base_amount,
                        },
                    ),
                ],
            }

            move = (
                env["account.move"]
                .sudo()
                .with_company(company_id)
                .create(move_vals)
            )
            move.action_post()

            # Wallet balance updates
            rider_card = (
                env["loyalty.card"]
                .sudo()
                .search(
                    [("partner_id", "=", rider.id)],
                    limit=1,
                )
            )
            driver_card = (
                env["loyalty.card"]
                .sudo()
                .search(
                    [("partner_id", "=", driver.id)],
                    limit=1,
                )
            )
            if not rider_card or not driver_card:
                return request.make_json_response(
                    {"status": 404, "message": "Wallet not found for rider or driver"},
                    status=404,
                )

            if amount > 0:
                # rider.wallet -= amount, driver.wallet += amount
                rider_card.caram_withdraw(
                    base_amount,
                    commission_amount=0.0,
                    fine_amount=0.0,
                    description=f"Wallet clearing to driver {driver.id}",
                    status="posted",
                    driver=driver,
                    should_create_invoice=False,
                    order_model="account.move",
                    order_id=move.id,
                    company_id=company_id,
                )
                driver_card.caram_addwallet(
                    base_amount,
                    description=f"Wallet clearing from rider {rider.id}",
                    status="posted",
                    driver=driver,
                    should_create_payment=False,
                    order_model="account.move",
                    order_id=move.id,
                    company_id=company_id,
                )
            else:
                # driver.wallet -= amount_abs, rider.wallet += amount_abs
                driver_card.caram_withdraw(
                    base_amount,
                    commission_amount=0.0,
                    fine_amount=0.0,
                    description=f"Wallet clearing to rider {rider.id}",
                    status="posted",
                    driver=driver,
                    should_create_invoice=False,
                    order_model="account.move",
                    order_id=move.id,
                    company_id=company_id,
                )
                rider_card.caram_addwallet(
                    base_amount,
                    description=f"Wallet clearing from driver {driver.id}",
                    status="posted",
                    driver=rider,
                    should_create_payment=False,
                    order_model="account.move",
                    order_id=move.id,
                    company_id=company_id,
                )

            response = {
                "status": 200,
                "journal_entry_id": move.id,
                "rider_id": rider.id,
                "driver_id": driver.id,
                "amount": amount,
                "direction": direction,
                "message": "Wallet clearing completed successfully",
            }
            return request.make_json_response(response, status=200)

        except Exception as e:
            return request.make_json_response(
                {"status": 500, "message": f"Internal server error: {str(e)}"},
                status=500,
            )

    @http.route("/api/ride/pay", type="http", auth="none", methods=["POST"], csrf=False)
    def pay_ride(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            user = self._authenticate()
            fare_amount = float(payload.get("fare_amount"))
            ride_id = payload.get("ride_id")
            wallet_paid = payload.get("wallet_paid", 0.0)
            coupon_value = payload.get("coupon_value", 0.0)
            coupon_description = payload.get("coupon_description")
            cash_paid = payload.get("cash_paid", 0.0)
            commission_amount = payload.get("commission_amount", 0.0)
            penalties = payload.get("penalties", []) or []
            rider_id = payload.get("rider_id")
            driver_id = payload.get("driver_id") or payload.get("driver")
            payment_mode = payload.get("payment_mode")
            accounting_date = payload.get("date") or False
            note_from_api = payload.get("note_from_api") or False
            is_airport_trip = payload.get("is_airport_trip", False)
            driver_type = payload.get("driver_type")
            expense_amount = payload.get("expense_amount", 0.0)
            api_payload = payload

            if not payload.get("company_id"):
                return request.make_json_response({"error": "company_id is required"}, status=400)
            try:
                company_id = int(payload.get("company_id"))
            except (TypeError, ValueError):
                return request.make_json_response({"error": "Invalid company_id"}, status=400)

            env = self._get_env(user, company_id)

            if driver_type == 'external' and not expense_amount:
                return request.make_json_response({"error": "expense_amount is required if driver_type is external"}, status=400)

            if not payment_mode:
                return request.make_json_response({"error": "payment_mode is required"}, status=400)
                
            if payment_mode not in ["cash_only", "cash_exceed", "wallet_paid", "wallet_cash"]:
                return request.make_json_response({"error": "Invalid payment_mode"}, status=400)
                
            if not ride_id:
                return request.make_json_response({"error": "ride_id is required"}, status=400)

            if fare_amount <= 0:
                return request.make_json_response({"error": "fare_amount must be > 0"}, status=400)

            # wallet_paid can be 0.0 (e.g. cash-only rides)
            if wallet_paid is None or float(wallet_paid) < 0:
                return request.make_json_response({"error": "wallet_paid is required and must be >= 0"}, status=400)
                
            #if not commission_amount or commission_amount <= 0:
                #return request.make_json_response({"error": "commission_amount is required"}, status=400)

            if not rider_id:
                return request.make_json_response({"error": "rider_id is required"}, status=400)
            if not driver_id:
                return request.make_json_response({"error": "driver_id is required"}, status=400)

            # -------------------- Find Rider and Driver --------------------
            rider = env["res.partner"].sudo().browse(rider_id)
            driver = env["res.partner"].sudo().browse(driver_id)
            if not rider.exists():
                return request.make_json_response({"error": "Rider not found"}, status=404)
            if not driver.exists():
                return request.make_json_response({"error": "Driver not found"}, status=404)

            # -------------------- Find Ride --------------------
            ride = env["caram.ride"].sudo().search(
                [("ride_id", "=", ride_id), ("company_id", "=", company_id)], limit=1
            )
            if not ride:
                ride = env["caram.ride"].sudo().with_company(company_id).create(
                    {
                        "ride_id": ride_id,
                        "company_id": company_id,
                        "rider_id": rider.id,
                        "driver_id": driver.id,
                        "fare_amount": fare_amount,
                        "commission_amount": commission_amount,
                        "wallet_paid": wallet_paid,
                        "cash_paid": cash_paid,
                        "paid_at": accounting_date or fields.Datetime.now(),
                    }
                )
            try:
                result = ride.with_company(company_id).action_pay_ride(
                    fare_amount=fare_amount,
                    wallet_paid=wallet_paid,
                    cash_paid=cash_paid,
                    commission_amount=commission_amount,
                    penalties=penalties,
                    payment_mode=payment_mode,
                    accounting_date=accounting_date,
                    note_from_api=note_from_api,
                    api_payload=api_payload,
                    is_airport_trip=is_airport_trip,
                    driver_type=driver_type,
                    expense_amount=expense_amount,
                    company_id=company_id,
                )
                if coupon_value>0:
                    # Wallet
                    card = (env["loyalty.card"].sudo().search( [("partner_id", "=", driver.id)], limit=1))
                    if not card:
                        return request.make_json_response(
                            {"status": 404, "message": "Wallet not found for this partner"}, status=404
                          )

                    # Bonus -> credit note using existing helper and compensation product expense account
                    move = self.create_driver_coupon_credit_note(
                    env,
                    company_id,
                    driver,
                    coupon_value,
                    coupon_description,
                    accounting_date=accounting_date,
                    note_from_api=note_from_api,
                    api_payload=api_payload,
                )
                    if not move:
                        return request.make_json_response(
                        {"status": 500, "message": "Failed to create welcome coupon credit note"},
                        status=500,
                    )
                     # -------------------- Wallet & loyalty history --------------------
                    balance_before = card.caram_get_posted_balance()
                    delta = coupon_value

                    tx_vals = {
                        "card_id": card.id,
                        "description": coupon_description,
                        "issued": delta,
                        "used": 0.0,
                        "status": "posted",
                        "order_model": "account.move",
                        "order_id": move.id,
                        "transaction_date": accounting_date or fields.Datetime.now(),
                    }
                    tx = env["loyalty.history"].sudo().create(tx_vals)

                    balance_after = card.caram_get_posted_balance()
                    card.sudo().write({"points": balance_after})

            
            except UserError as e:
                msg = str(e)
                if "Insufficient wallet balance" in msg:
                    return request.make_json_response(
                        {"status": "error", "code": "INSUFFICIENT_WALLET_BALANCE"}, status=409
                    )
                return request.make_json_response({"error": msg}, status=400)

            return request.make_json_response(result, status=200)

        except Exception as e:
            import traceback
            _logger.error(traceback.format_exc())
            return request.make_json_response({"error": f"Failed to pay ride: {str(e)}"}, status=500)