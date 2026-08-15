from odoo import api, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    @api.model
    def _payment_fields(self, order, ui_paymentline):
        """Persiste los datos de verificación Pabilo que manda el POS.

        OJO: este método vive en pos.order (no en pos.payment) — aquí se arma el
        dict con el que se crea el pos.payment.
        """
        fields = super()._payment_fields(order, ui_paymentline)
        fields.update({
            'pabilo_reference': ui_paymentline.get('pabilo_reference') or '',
            'pabilo_payment_id': ui_paymentline.get('pabilo_payment_id') or '',
            'pabilo_is_new': ui_paymentline.get('pabilo_is_new') or False,
        })
        return fields
