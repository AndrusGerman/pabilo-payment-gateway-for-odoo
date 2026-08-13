from odoo import fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    pabilo_reference = fields.Char(
        string='Referencia Pabilo',
        readonly=True,
        help='Referencia bancaria verificada vía Pabilo: pago móvil, '
             'transferencia o Binance.',
    )
    pabilo_payment_id = fields.Char(
        string='ID Pago Pabilo',
        readonly=True,
        help='ID del user_bank_payment en Pabilo, para trazabilidad cruzada.',
    )
    pabilo_is_new = fields.Boolean(
        string='Pago Nuevo',
        readonly=True,
        help='False si la referencia ya había sido verificada antes (posible duplicado).',
    )
