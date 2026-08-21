import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Cuánto tiempo sigue siendo reutilizable una verificación que nunca llegó a una
# venta cerrada. Una venta suspendida se retoma en minutos, no al día siguiente;
# pasada la ventana, la referencia vuelve a tratarse como ajena.
REUSE_WINDOW_HOURS = 24


class PabiloVerification(models.Model):
    """Bitácora de los pagos que ESTE Odoo verificó contra Pabilo.

    Existe por un caso concreto: el cajero valida una referencia, la venta se
    suspende o se sale del POS sin facturar, y al retomarla Pabilo responde que
    el movimiento ya fue consumido. Y tiene razón —lo consumimos nosotros hace un
    minuto—, pero sin memoria propia no hay forma de distinguir «esto ya lo cobró
    otra venta» de «esto lo verifiqué yo y la venta no se cerró».

    Con esta bitácora sí se distingue, y de paso la reutilización no gasta otro
    crédito de Pabilo ni espera otra consulta al banco.
    """
    _name = 'pabilo.verification'
    _description = 'Verificaciones de pago hechas contra Pabilo'
    _order = 'create_date desc'
    _rec_name = 'reference'

    reference = fields.Char(
        string='Referencia', required=True, index=True,
        help='Lo que el cajero teclea: los últimos dígitos de la referencia bancaria.')
    pabilo_payment_id = fields.Char(
        string='ID del Movimiento en Pabilo', index=True,
        help='Identificador del movimiento bancario en Pabilo. Es la clave fuerte: '
             'dos referencias pueden coincidir en sus últimos dígitos, este id no.')
    user_bank_id = fields.Many2one(
        'pabilo.user.bank', string='Cuenta Pabilo', required=True, ondelete='restrict')
    amount = fields.Monetary(
        string='Monto Verificado', currency_field='currency_id',
        help='Monto que se buscó en el banco, en la moneda de la cuenta.')
    currency_id = fields.Many2one('res.currency', string='Moneda')
    state = fields.Selection([
        ('verified', 'Verificado'),
        ('consumed', 'Cobrado en una venta'),
    ], string='Estado', default='verified', required=True, index=True)

    pos_order_uid = fields.Char(
        string='Pedido del POS', index=True,
        help='Identificador que el POS da al pedido en el navegador, antes de que '
             'exista en Odoo. Es lo que permite reconocer una venta suspendida.')
    pos_session_id = fields.Many2one('pos.session', string='Sesión POS', ondelete='set null')
    pos_order_id = fields.Many2one(
        'pos.order', string='Venta', ondelete='set null',
        help='La venta que finalmente cobró este movimiento. Mientras esté vacío, '
             'el pago se verificó pero no llegó a ninguna venta cerrada.')
    payment_method_id = fields.Many2one(
        'pos.payment.method', string='Método de Pago', ondelete='set null')
    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company)
    source_name = fields.Char(
        string='Caja y Cajero',
        help='Quién cobró, tal como se le informó a Pabilo.')
    reused_count = fields.Integer(
        string='Veces Reutilizada', default=0,
        help='Cuántas veces se aceptó esta verificación sin volver a consultar a '
             'Pabilo, por retomarse una venta suspendida.')

    @api.model
    def _references_match(self, stored, typed):
        """¿Estas dos referencias son la misma, tecleadas con distinto largo?

        Pabilo matchea **por sufijo**: el cajero teclea los últimos dígitos de la
        referencia bancaria, no el número completo. Así que la misma referencia
        puede llegar como `704777` una vez y como `4777` la siguiente, y comparar
        con `=` las tomaría por distintas — que es justo el fallo que esta bitácora
        tiene que evitar.

        Se comparan en los dos sentidos porque no se sabe cuál de las dos vino más
        corta.
        """
        a = (stored or '').strip()
        b = (typed or '').strip()
        if not a or not b:
            return False
        return a.endswith(b) or b.endswith(a)

    @api.model
    def _find_any(self, user_bank, reference):
        """Verificaciones propias de esta referencia y cuenta, la más nueva primero.

        Sirve para los dos casos: reconocer una venta suspendida y explicar que la
        referencia ya se cobró. No filtra por estado a propósito, para poder decir
        *cuál* venta la consumió.

        El filtro por sufijo se hace en Python: SQL no compara «esta columna es
        sufijo de este parámetro», y el conjunto por cuenta es de decenas de
        registros, no de miles.
        """
        candidatas = self.sudo().search([
            ('company_id', '=', self.env.company.id),
            ('user_bank_id', '=', user_bank.id),
        ])
        return candidatas.filtered(
            lambda v: self._references_match(v.reference, reference))

    @api.model
    def _find_by_payment_id(self, payment_id):
        """Verificación de un movimiento concreto de Pabilo.

        Es la clave fuerte y la que no depende de cuántos dígitos teclee el
        cajero: Pabilo devuelve el mismo `user_bank_payment.id` tanto cuando el
        movimiento es nuevo como cuando ya fue usado.
        """
        if not payment_id:
            return self.browse()
        return self.sudo().search([
            ('company_id', '=', self.env.company.id),
            ('pabilo_payment_id', '=', payment_id),
        ], limit=1)

    @api.model
    def _find_reusable(self, user_bank, reference, amount):
        """Verificación propia que se puede volver a aceptar, o un recordset vacío.

        Exige las cuatro cosas a la vez —misma cuenta, misma referencia, mismo
        monto y ninguna venta cerrada— porque cada una por separado es
        insuficiente: la referencia son sólo unos dígitos finales, y el monto por
        sí mismo no identifica nada.
        """
        limite = fields.Datetime.now() - timedelta(hours=REUSE_WINDOW_HOURS)
        candidatas = self._find_any(user_bank, reference).filtered(
            lambda v: v.state == 'verified'
            and not v.pos_order_id
            and v.create_date >= limite
        )
        # El monto se compara con tolerancia, no con ==: Pabilo redondea a dos
        # decimales y el mismo cobro puede volver con un céntimo de diferencia.
        for candidata in candidatas:
            if abs(candidata.amount - amount) < 0.005:
                return candidata
        return self.browse()

    @api.model
    def _log_verification(self, user_bank, reference, amount, currency, result,
                          pos_order_uid=None, payment_method=None, source_name=None):
        """Anota una verificación nueva. Nunca lanza: si la bitácora falla, el
        cobro ya está hecho y no puede caerse por no haberlo apuntado."""
        try:
            return self.sudo().create({
                'reference': reference,
                'pabilo_payment_id': result.get('payment_id') or '',
                'user_bank_id': user_bank.id,
                'amount': amount,
                'currency_id': currency.id if currency else False,
                'state': 'verified',
                'pos_order_uid': pos_order_uid or '',
                'pos_session_id': self._current_session_id(),
                'payment_method_id': payment_method.id if payment_method else False,
                'company_id': self.env.company.id,
                'source_name': source_name or '',
            })
        except Exception:
            _logger.exception(
                "Pabilo: no se pudo anotar la verificacion de la referencia %s", reference)
            return self.browse()

    @api.model
    def _current_session_id(self):
        sesion = self.env['pos.session'].sudo().search([
            ('state', '=', 'opened'),
            ('user_id', '=', self.env.user.id),
        ], limit=1)
        return sesion.id or False

    def _mark_consumed(self, order):
        """La venta se cerró: este movimiento ya no se puede reutilizar."""
        for rec in self:
            rec.sudo().write({'state': 'consumed', 'pos_order_id': order.id})
            _logger.info("Pabilo: referencia %s consumida por la venta %s",
                         rec.reference, order.name)

    def _note_reuse(self, pos_order_uid=None):
        """Se aceptó otra vez sin llamar a la API, por una venta retomada."""
        for rec in self:
            vals = {'reused_count': rec.reused_count + 1}
            if pos_order_uid:
                vals['pos_order_uid'] = pos_order_uid
            rec.sudo().write(vals)
            _logger.info(
                "Pabilo: se reutiliza la verificacion de %s (movimiento %s) sin "
                "volver a consultar a Pabilo; van %s veces",
                rec.reference, rec.pabilo_payment_id or '-', vals['reused_count'])

    def _consumed_message(self):
        """Mensaje para el cajero cuando la referencia ya fue cobrada.

        Nombrar la venta es la mitad del valor de esta bitácora: sin eso el cajero
        sólo sabe que «ya se usó», y con eso puede ir a verla. Pero el nombre hay
        que mirarlo antes de usarlo: Odoo deja `/` en los pedidos a los que la
        secuencia todavía no les asignó número, y decirle a alguien «cobrada en la
        venta /» es peor que no decir nada.
        """
        self.ensure_one()
        etiqueta = self.pos_order_id.name or ''
        if etiqueta in ('', '/'):
            etiqueta = self.pos_order_id.pos_reference or ''
        if etiqueta:
            return _('Esta referencia ya fue cobrada en la venta %s. '
                     'Pida al cliente un pago nuevo.', etiqueta)
        return _('Esta referencia ya fue verificada antes y cobrada en una venta. '
                 'Pida al cliente un pago nuevo.')
