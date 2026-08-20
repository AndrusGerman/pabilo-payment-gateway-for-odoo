from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    # Marca de qué cuenta de Pabilo salió este diario. Sirve para dos cosas:
    # que crear los métodos de pago sea idempotente (se busca por aquí en vez de
    # adivinar por nombre o código), y para que en contabilidad se vea de un
    # vistazo a qué cuenta bancaria real corresponde cada diario.
    pabilo_user_bank_id = fields.Many2one(
        'pabilo.user.bank',
        string='Cuenta Pabilo',
        readonly=True,
        ondelete='set null',
        help='Cuenta de Pabilo cuyos cobros se asientan en este diario. '
             'Lo rellena el módulo al crear el diario; no hace falta tocarlo.',
    )
