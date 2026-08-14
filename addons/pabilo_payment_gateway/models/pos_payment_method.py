import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    pabilo_user_bank_id = fields.Many2one(
        'pabilo.user.bank',
        string='Cuenta Pabilo por Defecto',
        help='La cuenta bancaria de Pabilo donde se verificarán los pagos recibidos en este POS.'
    )
    pabilo_account_hint = fields.Char(
        string='Cuenta Destino',
        compute='_compute_pabilo_account_hint',
        help='Texto de ayuda que ve el cajero en el POS: a qué cuenta debe llegar el pago.',
    )

    @api.depends('pabilo_user_bank_id', 'pabilo_user_bank_id.display_name')
    def _compute_pabilo_account_hint(self):
        for method in self:
            method.pabilo_account_hint = method.pabilo_user_bank_id.display_name or ''

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('pabilo', 'Pabilo')]

    def _is_write_forbidden(self, fields):
        whitelisted_fields = {'pabilo_user_bank_id'}
        return super()._is_write_forbidden(fields - whitelisted_fields)

    def pabilo_verify_payment(self, reference, amount, user_bank_id=None, source_name=None,
                              fecha_pago=None):
        """Verifica un pago contra la API de Pabilo. Llamado desde el POS.

        user_bank_id: cuenta elegida por el cajero en el POS (opcional). Si no
        llega, se usa la cuenta configurada en el método de pago.

        Devuelve un dict normalizado (ver pabilo.client.verify_payment); nunca
        lanza UserError para errores de dominio, así el cajero ve el motivo real
        en vez de un genérico "verifique su conexión".
        """
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Solo usuarios del Punto de Venta pueden verificar pagos Pabilo.'))

        user_bank = self.pabilo_user_bank_id
        if user_bank_id:
            candidate = self.env['pabilo.user.bank'].browse(user_bank_id).exists()
            if candidate and candidate.company_id == self.env.company and not candidate.is_trashed:
                user_bank = candidate
        if not user_bank:
            # Si no se configuró, buscar la primera disponible. Excluye las
            # eliminadas: la sincronización marca así las que ya no existen en
            # Pabilo, y verificar contra ellas fallaría siempre.
            user_bank = self.env['pabilo.user.bank'].search(
                [('company_id', '=', self.env.company.id), ('is_trashed', '=', False)],
                limit=1,
            )
        if not user_bank:
            return {
                'verified': False,
                'status': 'failed',
                'payment_id': '',
                'is_new': False,
                'credit_cost': 0.0,
                'error_code': 'NO_USER_BANK',
                'message': _('No hay cuentas bancarias de Pabilo configuradas. '
                             'Sincronízalas desde los Ajustes.'),
            }

        # Nombre del origen: lo que manda el POS (caja + cajero) o, si no llegó,
        # al menos el usuario, para que el pago sea rastreable desde Pabilo.
        source = (source_name or '').strip() or self.env.user.name or ''

        # Por defecto el dia de hoy en la zona del usuario, que es el caso normal
        # en una caja. Se valida aqui para no gastar una llamada a la API en un
        # 400 por formato.
        fecha = (fecha_pago or '').strip()
        if not fecha:
            fecha = fields.Date.to_string(fields.Date.context_today(self))
        else:
            try:
                datetime.strptime(fecha, '%Y-%m-%d')
            except ValueError:
                return {
                    'verified': False, 'status': 'failed', 'payment_id': '',
                    'is_new': False, 'credit_cost': 0.0,
                    'error_code': 'INVALID_DATE',
                    'message': _('La fecha debe tener el formato AAAA-MM-DD.'),
                }

        return self.env['pabilo.client'].verify_payment(
            user_bank, reference, amount, source_name=source[:120], fecha_pago=fecha)

    def pabilo_get_user_banks(self):
        """Cuentas Pabilo disponibles para elegir en el POS al cobrar."""
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Solo usuarios del Punto de Venta pueden consultar las cuentas Pabilo.'))
        # Refresca solo si hace falta. Así una cuenta agregada en Pabilo aparece
        # en el POS sin que nadie tenga que acordarse de pulsar "Sincronizar".
        self.env['pabilo.user.bank']._sync_if_stale()
        banks = self.env['pabilo.user.bank'].search([
            ('company_id', '=', self.env.company.id),
            ('is_trashed', '=', False),
        ])
        return [{'id': bank.id, 'display_name': bank.display_name} for bank in banks]
