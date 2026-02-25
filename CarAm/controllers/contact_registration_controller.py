# -*- coding: utf-8 -*-

from odoo import http
from odoo import models, fields, api  # Add this line at the top
from odoo.http import request
from odoo.exceptions import UserError
import json


class ContactRegistrationController(http.Controller):
    
    def _get_env(self, user):
      company = user.company_id
      return request.env(
        user=user,
        context=dict(
            request.env.context,
            allowed_company_ids=[company.id],
            company_id=company.id,
        )
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


    def create_driver_coupon_credit_note(self, env, company_id, partner, amount):
        """Create & post a customer credit note to represent the welcome coupon."""
        product_coupon = env['product.product'].sudo().with_company(company_id).search(
            [('is_coupon', '=', True)],
            limit=1,
        )
        if not product_coupon:
            return False

        expense_account = (
            product_coupon.property_account_expense_id
            or product_coupon.categ_id.property_account_expense_id
        )
        if not expense_account:
            expense_account = env['account.account'].sudo().with_company(company_id).search(
                [('company_id', '=', company_id), ('account_type', 'in', ('expense'))],
                limit=1,
            )
        if not expense_account:
            return False

        credit_note = env['account.move'].sudo().with_company(company_id).create({
            'partner_id': partner.id,
            'move_type': 'out_refund',
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [(0, 0, {
                'product_id': product_coupon.id,
                'account_id': expense_account.id,
                'name': 'Welcome Coupon - Service Credit',
                'quantity': 1,
                'price_unit': amount,
            })],

            'is_from_api': True,
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

            # -------------------- Check Existing Contact --------------------
            domain = []
            if sub_id:
                domain.append(('sub_id', '=', sub_id))
            if mobile:
                domain.append(('mobile', '=', mobile))

            domain.append(('company_id', '=', company_id))
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
                    "company_id": company_id,
                    "customer_rank": 1,
                    "type": "contact",
                    "user_id": user.id,
                }

                partner = env["res.partner"].sudo().create(partner_vals)

            # -------------------- Create Wallet --------------------
            program = env["loyalty.program"].sudo().search([
                ("program_type", "=", "ewallet"),
                ("company_id", "=", company_id),
            ], limit=1)

            if not program:
                return request.make_json_response({"error": "e-Wallet program not found"}, status=500)

            card = env["loyalty.card"].sudo().create({"program_id": program.id, "partner_id": partner.id})
            wallet_balance = 0.0

            if coupon_value > 0:
                credit_note = self.create_driver_coupon_credit_note(env, company_id, partner, coupon_value)
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
                "contact_type": partner.contact_type or ""
            }

            return request.make_json_response({"status": "success", "message": "Contact updated successfully", "data": data}, status=200)

        except Exception as e:
            return request.make_json_response({"error": f"Failed to update contact: {str(e)}"}, status=500)

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
            transaction_type = payload.get("transaction_type")
            amount = payload.get("amount") 
            reference = payload.get("reference")
            bank = payload.get("bank")
            image_url = payload.get("image_url") or payload.get("Image_url")
            note = payload.get("note")
            account_number = payload.get("account_number")

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
                    move_credit = wallet.create_points_credit_note(env, company_id, partner, amount)
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
                    "order_model": "account.payment",
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
            bank = payload.get("bank")
            account_number = payload.get("account_number")
            note = payload.get("note", "")
            
            # -------------------- Validate required fields --------------------
            if not partner_id:
                return request.make_json_response({"error": "odoo_partner_id is required"}, status=400)
            if not amount or amount <= 0:
                return request.make_json_response({"error": "amount is required and must be greater than 0"}, status=400)
            if not transaction_id:
                return request.make_json_response({"error": "transaction_id is required"}, status=400)

            partner = env['res.partner'].sudo().browse(partner_id)
            if not partner:
                return request.make_json_response({"error": "Partner not found"}, status=404)

            wallet = env['loyalty.card'].sudo().search([('partner_id', '=', partner.id), ('company_id', '=', company_id)], limit=1)
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
            
            if net_amount > total_balance:
                return request.make_json_response({"error": "Insufficient wallet balance"}, status=409)
            
            # -------------------- Create Journal Entry --------------------
            contact_type = partner.contact_type
            bank_account, liability_account, error_response = self._get_wallet_accounts(env, company_id, contact_type, coupon_value=0)
            if error_response:
                return error_response

            move = None
            if bank_account and liability_account:
                ref = note
                should_post = False
                state = 'draft'
                payment_method_type = 'bank'
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


    @http.route("/api/ride/pay", type="http", auth="none", methods=["POST"], csrf=False)
    def pay_ride(self, **kw):
        try:
            payload = json.loads(request.httprequest.data.decode("utf-8"))
            user = self._authenticate()
            env = self._get_env(user)
            company_id = user.company_id.id
            fare_amount = float(payload.get("fare_amount"))
            ride_id = payload.get("ride_id")
            wallet_paid = payload.get("wallet_paid", 0.0)
            cash_paid = payload.get("cash_paid", 0.0)
            commission_amount = payload.get("commission_amount", 0.0)
            penalties = payload.get("penalties", []) or []
            rider_id = payload.get("rider_id")
            driver_id = payload.get("driver_id") or payload.get("driver")
            payment_mode = payload.get("payment_mode")

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
                    }
                )
            try:
                result = ride.action_pay_ride(
                    fare_amount=fare_amount,
                    wallet_paid=wallet_paid,
                    cash_paid=cash_paid,
                    commission_amount=commission_amount,
                    penalties=penalties,
                    payment_mode=payment_mode,
                )
            except UserError as e:
                msg = str(e)
                if "Insufficient wallet balance" in msg:
                    return request.make_json_response(
                        {"status": "error", "code": "INSUFFICIENT_WALLET_BALANCE"}, status=409
                    )
                return request.make_json_response({"error": msg}, status=400)

            return request.make_json_response(result, status=200)

        except Exception as e:
            return request.make_json_response({"error": f"Failed to pay ride: {str(e)}"}, status=500)