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

    pabilo_amount_confirm = fields.Boolean(
        string='Elegir Como se Valida en el POS',
        default=True,
        help='Cuando haya que convertir el monto, antes de pedir la referencia el '
             'POS muestra cuanto se va a buscar en el banco y con que tasa salio, '
             'y deja elegir: aceptarlo, usar otra tasa o escribir el monto a mano. '
             'Desactivalo solo si la tasa de Odoo es siempre exactamente la que se '
             'le cobro al cliente.\n\n'
             'Si la moneda del POS ya es la del banco no hay nada que elegir y no '
             'se pregunta.',
    )
    pabilo_alt_rate_field = fields.Char(
        string='Campo de la Tasa Alterna',
        help='Dejalo vacio salvo que uses un modulo de moneda alterna que NO se '
             'apoye en las tasas de Odoo, sino en una propia (las localizaciones '
             'venezolanas suelen pintar un "Restante alterno" con ella). Escribe '
             'aqui donde leerla en el POS y se propondra esa en vez de la de Odoo, '
             'para que el monto coincida con lo que ve el cajero en pantalla.\n\n'
             'Es una ruta con puntos que se busca, en orden, en la linea de pago, '
             'en el pedido y en el objeto pos: por ejemplo "tasa_bcv" o '
             '"config.tasa_del_dia".',
    )

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('pabilo', 'Pabilo')]

    def _is_write_forbidden(self, fields):
        # Odoo prohibe tocar un metodo de pago con sesiones abiertas, porque
        # cambiarlo a media sesion descuadra el cierre. Estos tres no: no entran
        # en ningun asiento, solo dicen contra que cuenta se verifica y como se
        # resuelve el monto en pantalla. Obligar a cerrar la caja para corregir
        # una tasa mal escrita seria absurdo.
        whitelisted_fields = {
            'pabilo_user_bank_id',
            'pabilo_amount_confirm',
            'pabilo_alt_rate_field',
        }
        return super()._is_write_forbidden(fields - whitelisted_fields)

    # ==========================================
    # Moneda: el POS cobra en la suya, el banco registra en la suya
    # ==========================================
    #
    # Las tasas son las nativas de Odoo (`res.currency.rate`, o sea Contabilidad
    # -> Configuracion -> Monedas -> Tasas): las mismas que usa el resto del
    # sistema para facturar y contabilizar, no un invento de este modulo. Por eso
    # la conversion es `res.currency._convert` y no una multiplicacion propia.
    #
    # Sobre eso hay dos escapes, porque la tasa contable no siempre es la que se
    # le cobro al cliente en el mostrador:
    #   - `pabilo_alt_rate_field`, para leer la tasa de un modulo de moneda
    #     alterna que lleve la suya;
    #   - la confirmacion en el POS, donde el cajero puede cambiar la tasa o
    #     escribir el monto tal como aparece en el comprobante del cliente.

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

    def _pabilo_conversion_rate(self, from_currency, to_currency, date):
        """Tasa nativa de Odoo para pasar `from_currency` a `to_currency`, o 0.0
        si Odoo no la sabe.

        Punto de extension: un modulo que lleve su propia tasa solo tiene que
        heredar este metodo y devolverla; el resto del flujo sigue igual. Desde el
        POS se puede apuntar a ella sin escribir codigo, con el campo
        `pabilo_alt_rate_field`.

        Devolver 0.0 en vez de dejar que Odoo responda 1.0 es deliberado:
        `_get_rates` hace COALESCE(..., 1.0) cuando no encuentra tasa, asi que
        `_convert` no protesta y devuelve el monto intacto. Ese fallo seria
        invisible y se mandarian dolares creyendo que son bolivares.
        """
        self.ensure_one()
        company = self.env.company
        for currency in (from_currency, to_currency):
            # La moneda de la compania vale 1 por definicion y no lleva filas.
            if currency == company.currency_id:
                continue
            if not self.env['res.currency.rate'].sudo().search_count([
                    ('currency_id', '=', currency.id),
                    ('company_id', 'in', (False, company.id))]):
                return 0.0
        return self.env['res.currency']._get_conversion_rate(
            from_currency, to_currency, company, date)

    def _pabilo_resolve_amount(self, user_bank, amount, pos_currency_id=None,
                               alt_rate=None, amount_in_bank_currency=None):
        """Monto que se le va a pedir al banco, y de donde salio.

        Pabilo compara contra el movimiento bancario, que esta en la moneda de la
        cuenta: bolivares en los bancos venezolanos, dolares en Binance. Con
        multi-moneda -precios en dolares, banco en bolivares- mandar el monto de
        la linea tal cual da siempre PAYMENT_AMOUNT_NOT_VALID, porque compara
        0,60 contra los 36,00 que llegaron al banco.

        Por orden de prioridad:
          1. lo que confirmo el cajero (`amount_in_bank_currency`)
          2. una tasa que mando el POS (`alt_rate`): la que el cajero eligio, o la
             del modulo de moneda alterna
          3. la tasa nativa de Odoo

        Devuelve un dict con amount, currency, source, rate y error.
        """
        self.ensure_one()
        bank_currency = user_bank.currency_id
        pos_currency = (self.env['res.currency'].browse(pos_currency_id).exists()
                        if pos_currency_id else self.env['res.currency'])

        def resultado(monto, source, rate=0.0, error=None, currency=None):
            return {
                # Pabilo compara siempre con dos decimales, sin importar el
                # `rounding` que tengan configurado las monedas en Odoo.
                'amount': float_round(monto, precision_digits=2),
                'currency': currency if currency is not None else bank_currency,
                'source': source,
                'rate': rate,
                'error': error,
            }

        # 1) El cajero ya lo confirmo: manda su numero. No se vuelve a convertir.
        #    Que el monto lo diga el navegador no abre ningun hueco: el cajero ya
        #    decide el monto de la linea, y quien valida de verdad es Pabilo
        #    contra el movimiento del banco. Un numero equivocado se rechaza.
        if amount_in_bank_currency is not None:
            return resultado(amount_in_bank_currency, 'manual')

        if not pos_currency:
            # Llamada sin moneda de origen: JS viejo en cache tras actualizar el
            # modulo. No se adivina una, porque convertir desde la moneda
            # equivocada es peor que no convertir. Se manda tal cual, como antes.
            _logger.warning("Pabilo: verificacion sin moneda del POS; "
                            "el monto se envia sin convertir")
            return resultado(amount, 'sin_moneda')

        if not bank_currency:
            return resultado(0.0, 'error', currency=pos_currency, error=self._pabilo_error(
                'NO_BANK_CURRENCY',
                _('La moneda en la que esta cuenta registra los movimientos no '
                  'existe en Odoo. Activala en Contabilidad / Configuracion / '
                  'Monedas.')))

        # 2) La moneda del POS ya es la del banco: no hay nada que convertir.
        if bank_currency == pos_currency:
            return resultado(amount, 'igual')

        # 3) La tasa que mando el POS, y si no la nativa de Odoo.
        if alt_rate and alt_rate > 0:
            return resultado(amount * alt_rate, 'alterno', rate=alt_rate)

        rate = self._pabilo_conversion_rate(
            pos_currency, bank_currency, fields.Date.context_today(self))
        if not rate:
            return resultado(0.0, 'error', error=self._pabilo_error(
                'NO_CURRENCY_RATE',
                _('Odoo no tiene tasa de cambio para pasar el monto de %(desde)s a '
                  '%(hasta)s. Cargala en Contabilidad / Configuracion / Monedas / '
                  'Tasas.',
                  desde=pos_currency.name, hasta=bank_currency.name)))
        return resultado(amount * rate, 'tasa', rate=rate)

    def pabilo_amount_preview(self, amount, user_bank_id=None, pos_currency_id=None,
                              alt_rate=None, amount_in_bank_currency=None):
        """Monto que se verificara contra el banco, ya convertido y formateado.

        El POS lo muestra antes de pedir la referencia, junto con la tasa que lo
        produjo, para que el cajero lo compare con el comprobante del cliente: con
        multi-moneda la linea de pago dice 0,60 $ y al banco llegaron 36,00 Bs.

        `needs_confirm` le dice al POS si tiene que ofrecer elegir. Se pregunta
        solo cuando hubo conversion: si la moneda del POS ya es la del banco no
        hay nada que elegir y se ahorra un toque por cobro.
        """
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Solo usuarios del Punto de Venta pueden verificar pagos Pabilo.'))

        base = {'error_code': '', 'message': '', 'amount': amount, 'currency_name': '',
                'label': '', 'converted': False, 'needs_confirm': False,
                'source': '', 'rate': 0.0, 'line_label': '', 'pos_currency_name': '',
                'rate_label': ''}

        user_bank = self._pabilo_resolve_user_bank(user_bank_id)
        if not user_bank:
            return dict(base, error_code='NO_USER_BANK',
                        message=_('No hay cuentas bancarias de Pabilo configuradas. '
                                  'Sincronizalas desde los Ajustes.'))

        res = self._pabilo_resolve_amount(
            user_bank, amount, pos_currency_id, alt_rate, amount_in_bank_currency)
        if res['error']:
            return dict(base, error_code=res['error']['error_code'],
                        message=res['error']['message'])

        currency = res['currency']
        pos_currency = (self.env['res.currency'].browse(pos_currency_id).exists()
                        if pos_currency_id else self.env['res.currency'])
        convertido = res['source'] in ('tasa', 'alterno', 'manual')
        rate_label = ''
        if res['rate'] and pos_currency:
            # "60,00 Bs por cada USD": la unidad importa, porque en Venezuela la
            # tasa se dice en los dos sentidos y confundirla invierte la cuenta.
            rate_label = _('%(tasa)s %(destino)s por cada %(origen)s',
                           tasa=formatLang(self.env, res['rate'], digits=4),
                           destino=currency.name, origen=pos_currency.name)

        return {
            'error_code': '',
            'message': '',
            'amount': res['amount'],
            'currency_name': currency.name or '',
            'pos_currency_name': pos_currency.name or '',
            'label': formatLang(self.env, res['amount'], currency_obj=currency) if currency else '',
            'line_label': (formatLang(self.env, amount, currency_obj=pos_currency)
                           if pos_currency else ''),
            'converted': convertido,
            # Ya eligio: no se le vuelve a preguntar lo mismo.
            'needs_confirm': bool(res['source'] in ('tasa', 'alterno')
                                  and self.pabilo_amount_confirm
                                  and amount_in_bank_currency is None),
            'source': res['source'],
            'rate': res['rate'],
            'rate_label': rate_label,
        }

    def pabilo_verify_payment(self, reference, amount, user_bank_id=None, source_name=None,
                              fecha_pago=None, pos_currency_id=None, alt_rate=None,
                              amount_in_bank_currency=None):
        """Verifica un pago contra la API de Pabilo. Llamado desde el POS.

        user_bank_id: cuenta elegida por el cajero en el POS (opcional). Si no
        llega, se usa la cuenta configurada en el metodo de pago.

        pos_currency_id: moneda en la que viene `amount`, o sea la de la linea de
        pago del POS. La conversion a la moneda del banco se hace aqui y no en el
        navegador, que no tiene las tasas de Odoo.

        alt_rate: tasa que mando el POS, sea la que eligio el cajero o la del
        modulo de moneda alterna. Sin ella se usa la de Odoo.

        amount_in_bank_currency: monto que confirmo el cajero. Si llega, se manda
        ese y no se convierte nada.

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

        res = self._pabilo_resolve_amount(
            user_bank, amount, pos_currency_id, alt_rate, amount_in_bank_currency)
        if res['error']:
            return res['error']

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

        _logger.info("Pabilo verify ref=%s monto=%s %s (origen del monto: %s)",
                     reference, res['amount'], res['currency'].name, res['source'])
        return self.env['pabilo.client'].verify_payment(
            user_bank, reference, res['amount'], source_name=source[:120], fecha_pago=fecha)

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
