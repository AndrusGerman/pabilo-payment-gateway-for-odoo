import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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

    @api.model_create_multi
    def create(self, vals_list):
        """Al cerrarse la venta, la verificación pasa a consumida.

        Es el único momento en que se sabe que el movimiento de verdad se cobró.
        Hasta aquí la verificación se podía reutilizar —una venta suspendida que
        se retoma—; desde aquí ya no, y el próximo intento con esa referencia se
        rechaza nombrando esta venta.
        """
        payments = super().create(vals_list)
        Bitacora = self.env['pabilo.verification']
        for payment in payments:
            if not payment.pabilo_payment_id and not payment.pabilo_reference:
                continue
            banco = payment.payment_method_id.pabilo_user_bank_id
            if not banco:
                continue
            # El id del movimiento manda: dos referencias pueden coincidir en sus
            # ultimos digitos, ese id no. La referencia queda de respaldo para
            # pagos anotados antes de que existiera la bitacora.
            verificaciones = Bitacora.sudo().browse()
            if payment.pabilo_payment_id:
                verificaciones = Bitacora.sudo().search([
                    ('pabilo_payment_id', '=', payment.pabilo_payment_id),
                    ('state', '=', 'verified'),
                ])
            if not verificaciones and payment.pabilo_reference:
                verificaciones = Bitacora._find_any(
                    banco, payment.pabilo_reference).filtered(
                        lambda v: v.state == 'verified')
            if verificaciones:
                verificaciones._mark_consumed(payment.pos_order_id)
        return payments
