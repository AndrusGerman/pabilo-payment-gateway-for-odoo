from odoo import fields, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('pabilo', 'Pabilo')],
        ondelete={'pabilo': 'set default'}
    )
    pabilo_api_key = fields.Char(
        string='Pabilo API Key (appKey)',
        required_if_provider='pabilo',
        groups='base.group_system',
    )
