import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import float_round
from odoo.tools.misc import formatLang

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

    # ==========================================
    # Moneda: el POS cobra en la suya, el banco registra en la suya
    # ==========================================

    def _pabilo_error(self, error_code, message):
        """Fallo con la misma forma que devuelve pabilo.client, para que el POS
        no tenga que distinguir si el error vino de Odoo o de la API."""
        return {
            'verified': False,
            'status': 'failed',
            'payment_id': '',
            'is_new': False,
            'credit_cost': 0.0,
            'error_code': error_code,
            'message': message,
        }

    def _pabilo_resolve_user_bank(self, user_bank_id=None):
        """Cuenta contra la que se verifica: la que eligio el cajero, la del
        metodo de pago o, si no hay ninguna, la primera disponible.

        Excluye las eliminadas: la sincronizacion marca asi las que ya no existen
        en Pabilo, y verificar contra ellas fallaria siempre.
        """
        self.ensure_one()
        if user_bank_id:
            candidate = self.env['pabilo.user.bank'].browse(user_bank_id).exists()
            if candidate and candidate.company_id == self.env.company and not candidate.is_trashed:
                return candidate
        if self.pabilo_user_bank_id:
            return self.pabilo_user_bank_id
        return self.env['pabilo.user.bank'].search(
            [('company_id', '=', self.env.company.id), ('is_trashed', '=', False)],
            limit=1,
        )

    def _pabilo_has_conversion_rate(self, currency, company):
        """True si Odoo sabe convertir esta moneda: la de la compania siempre
        vale 1, el resto necesita al menos una tasa cargada.

        Sin esta comprobacion el fallo seria invisible. `_convert` no protesta
        cuando no encuentra tasa: `_get_rates` hace COALESCE(..., 1.0) y devuelve
        el monto intacto, asi que el POS mandaria dolares creyendo que son
        bolivares y Pabilo responderia un desconcertante "monto no valido".
        """
        if not currency or currency == company.currency_id:
            return True
        return bool(self.env['res.currency.rate'].sudo().search_count([
            ('currency_id', '=', currency.id),
            ('company_id', 'in', (False, company.id)),
        ]))

    def _pabilo_amount_in_bank_currency(self, user_bank, amount, pos_currency_id=None):
        """Pasa el monto de la linea de pago a la moneda del banco.

        Pabilo compara contra el movimiento bancario, que esta en la moneda de la
        cuenta: bolivares en los bancos venezolanos, dolares en Binance. Con
        multi-moneda -precios en dolares, banco en bolivares- mandar el monto de
        la linea tal cual da siempre PAYMENT_AMOUNT_NOT_VALID, porque compara
        0,60 contra los 36,00 que llegaron al banco.

        Devuelve (monto, moneda_del_banco, error), con error=None si se convirtio.
        """
        self.ensure_one()
        company = self.env.company
        bank_currency = user_bank.currency_id
        pos_currency = (self.env['res.currency'].browse(pos_currency_id).exists()
                        if pos_currency_id else self.env['res.currency'])

        # Pabilo compara siempre con dos decimales, sin importar el `rounding`
        # que tengan configurado las monedas en Odoo.
        amount = float_round(amount, precision_digits=2)

        if not pos_currency:
            # Llamada sin moneda de origen: JS viejo en cache tras actualizar el
            # modulo. No se adivina una, porque convertir desde la moneda
            # equivocada es peor que no convertir. Se manda tal cual, como antes.
            _logger.warning("Pabilo: verificacion sin moneda del POS; "
                            "el monto se envia sin convertir")
            return amount, bank_currency, None

        if not bank_currency:
            return 0.0, pos_currency, self._pabilo_error(
                'NO_BANK_CURRENCY',
                _('La moneda en la que esta cuenta registra los movimientos no '
                  'existe en Odoo. Activala en Contabilidad / Configuracion / '
                  'Monedas.'))

        if bank_currency == pos_currency:
            return amount, bank_currency, None

        for currency in (pos_currency, bank_currency):
            if not self._pabilo_has_conversion_rate(currency, company):
                return 0.0, bank_currency, self._pabilo_error(
                    'NO_CURRENCY_RATE',
                    _('No hay tasa de cambio cargada para %(falta)s, asi que Odoo no '
                      'puede pasar el monto de %(desde)s a %(hasta)s. Cargala en '
                      'Contabilidad / Configuracion / Monedas.',
                      falta=currency.name, desde=pos_currency.name,
                      hasta=bank_currency.name))

        converted = pos_currency._convert(
            amount, bank_currency, company, fields.Date.context_today(self), round=False)
        return float_round(converted, precision_digits=2), bank_currency, None

    def pabilo_amount_preview(self, amount, user_bank_id=None, pos_currency_id=None):
        """Monto que se verificara contra el banco, ya convertido y formateado.

        El POS lo muestra antes de pedir la referencia, para que el cajero
        confirme contra lo que el cliente transfirio: con multi-moneda la linea
        de pago dice 0,60 $ y al banco llegaron 36,00 Bs. Solo informa; el monto
        que se verifica lo vuelve a calcular pabilo_verify_payment.
        """
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Solo usuarios del Punto de Venta pueden verificar pagos Pabilo.'))

        fallback = {'error_code': '', 'message': '', 'amount': amount,
                    'currency_name': '', 'label': '', 'converted': False}

        user_bank = self._pabilo_resolve_user_bank(user_bank_id)
        if not user_bank:
            return dict(fallback, error_code='NO_USER_BANK',
                        message=_('No hay cuentas bancarias de Pabilo configuradas. '
                                  'Sincronizalas desde los Ajustes.'))

        converted, currency, error = self._pabilo_amount_in_bank_currency(
            user_bank, amount, pos_currency_id)
        if error:
            return dict(fallback, error_code=error['error_code'], message=error['message'])

        return {
            'error_code': '',
            'message': '',
            'amount': converted,
            'currency_name': currency.name or '',
            'label': formatLang(self.env, converted, currency_obj=currency) if currency else '',
            'converted': bool(currency) and currency.id != pos_currency_id,
        }

    def pabilo_verify_payment(self, reference, amount, user_bank_id=None, source_name=None,
                              fecha_pago=None, pos_currency_id=None):
        """Verifica un pago contra la API de Pabilo. Llamado desde el POS.

        user_bank_id: cuenta elegida por el cajero en el POS (opcional). Si no
        llega, se usa la cuenta configurada en el metodo de pago.

        pos_currency_id: moneda en la que viene `amount`, o sea la de la linea de
        pago del POS. La conversion a la moneda del banco se hace aqui y no en el
        navegador, que no tiene las tasas de Odoo.

        Devuelve un dict normalizado (ver pabilo.client.verify_payment); nunca
        lanza UserError para errores de dominio, asi el cajero ve el motivo real
        en vez de un generico "verifique su conexion".
        """
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Solo usuarios del Punto de Venta pueden verificar pagos Pabilo.'))

        user_bank = self._pabilo_resolve_user_bank(user_bank_id)
        if not user_bank:
            return self._pabilo_error(
                'NO_USER_BANK',
                _('No hay cuentas bancarias de Pabilo configuradas. '
                  'Sincronizalas desde los Ajustes.'))

        amount, bank_currency, error = self._pabilo_amount_in_bank_currency(
            user_bank, amount, pos_currency_id)
        if error:
            return error

        # Nombre del origen: lo que manda el POS (caja + cajero) o, si no llego,
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
                return self._pabilo_error(
                    'INVALID_DATE', _('La fecha debe tener el formato AAAA-MM-DD.'))

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
