# -*- coding: utf-8 -*-
{
    'name': 'CarAm - Contact Registration & Wallet Withdrawal System',
    'version': '18.0.0.0',
    'category': 'API/Contact Management',
    'summary': 'API for registering drivers and riders with wallet withdrawal system',
    'description': """
        CarAm Module
        ============
        This module provides API endpoints for:
        - Registering contacts (drivers/riders)
        - Managing e-Wallet withdrawal system
        - Coupon system integration
        - Transaction management
    """,
    'author': 'Eng. Kewthar Naser',
    'depends': [
        'base',
        'contacts',
        'sale_loyalty',
        'loyalty',
        'account',
        'sale',
        'sale_subscription',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/res_partner_views.xml',
        'views/loyality_history_views.xml',
        'wizards/loyalty_history_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/sale_subscription_plan_views.xml',
        'views/account_journal_views.xml',
        'views/account_payment_views.xml',
        'views/product_template_views.xml',
        'views/caram_ride_views.xml',
        'views/caram_menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}

