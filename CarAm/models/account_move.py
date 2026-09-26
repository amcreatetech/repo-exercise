from odoo import models, fields


class AccountMove(models.Model):
    _inherit = "account.move"

    driver_id = fields.Many2one(
        "res.partner",
        string="Driver",
        index=True,
        help="The driver associated with this ride, independent of the invoice partner.",
    )
    ride_id = fields.Many2one(
        "caram.ride",
        string="Ride ID",
        index=True,
        help="External ride identifier from the mobile app / API.",
    )

    is_from_api = fields.Boolean(
        string="Created from API",
        default=False,
        copy=False,
        help="Indicates if this invoice, credit note, or journal entry was created from API",
        readonly=True,
    )
    
    note_from_api = fields.Text(
        string="Note from API",
        copy=False,
        readonly=True,
        help="Optional note sent by the external API when this move was created.",
    )
    api_payload = fields.Text(
        string="API Payload",
        copy=False,
        readonly=True,
        help="Full JSON request body received from the API when this move was created.",
    )

