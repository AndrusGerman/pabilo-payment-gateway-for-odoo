from odoo import models


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _loader_params_pos_payment_method(self):
        """Carga en el POS la cuenta Pabilo del método de pago y su texto de
        ayuda, sin tener que cargar el modelo pabilo.user.bank completo."""
        params = super()._loader_params_pos_payment_method()
        params['search_params']['fields'] = params['search_params']['fields'] + [
            'pabilo_user_bank_id',
            'pabilo_account_hint',
            'pabilo_amount_confirm',
            'pabilo_alt_rate_field',
        ]
        return params
