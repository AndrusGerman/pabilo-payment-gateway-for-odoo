import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Única puerta por la que se permite escribir en el espejo local: la sincronización.
SYNC_CONTEXT_KEY = 'pabilo_sync'

# Moneda en la que cada proveedor registra sus movimientos. Los bancos
# venezolanos operan en bolivares; Binance Pay, en dolares. Se deduce del
# proveedor y no se guarda: la API de Pabilo no devuelve la moneda de la cuenta
# y el espejo es de solo lectura, asi que no habria donde escribirla.
BANK_CURRENCY_BY_PROVIDER = {
    'BINANCE_APP': ('USD',),
}
# VEF es el codigo viejo del bolivar. Muchas bases venezolanas siguen con el,
# asi que se acepta como alternativa, pero VES manda si estan los dos.
DEFAULT_BANK_CURRENCY_NAMES = ('VES', 'VEF')


class PabiloUserBank(models.Model):
    """Espejo local de solo lectura de las cuentas bancarias de Pabilo.

    La fuente de verdad es Pabilo. Editar aquí solo produciría divergencia
    silenciosa: el siguiente `action_sync_banks` sobrescribiría el cambio sin
    avisar, y mientras tanto el POS verificaría contra una cuenta que no es la
    que muestra Odoo. Por eso el modelo solo se deja escribir desde la propia
    sincronización, y la ACL no concede write/create/unlink a ningún grupo.
    """
    _name = 'pabilo.user.bank'
    _description = 'Cuentas Bancarias de Pabilo'
    _rec_name = 'display_name'

    name = fields.Char(string='Descripción', required=True)
    pabilo_id = fields.Char(string='ID en Pabilo', required=True, index=True)
    provider = fields.Selection([
        ('VE_BAN', 'BDV Personal'),
        ('VE_BAN_EMP', 'BDV Empresa v1'),
        ('VE_BAN_EMP_V2', 'BDV Empresa v2'),
        ('VE_PROV', 'Provincial Personal'),
        ('VE_PROV_EMP', 'Provincial Empresa'),
        ('MERCANTIL_EMP_V1', 'Mercantil Empresa'),
        ('MERCANTIL_EMP_TEST_V1', 'Mercantil Empresa (Test)'),
        ('VE_BANK_PLAZA_V1', 'Banco Plaza'),
        ('VE_BANK_PLAZA_QA_V1', 'Banco Plaza QA'),
        ('VE_BANESCO_V1', 'Banesco'),
        ('VE_BANESCO_QA_V1', 'Banesco QA'),
        ('BINANCE_APP', 'Binance Pay'),
        ('NOTIFICATION_ACCOUNT', 'Notificación'),
        ('BANK_TEST', 'Test'),
    ], string='Proveedor Bancario')
    account_number = fields.Char(string='Número de Cuenta')
    account_type = fields.Char(string='Tipo de Cuenta')
    supports_payment_link = fields.Boolean(string='Soporta Enlaces de Pago', default=True)
    is_trashed = fields.Boolean(string='Eliminado', default=False)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    display_name = fields.Char(string='Nombre', compute='_compute_display_name', store=True)
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda de los Movimientos',
        compute='_compute_currency_id',
        help='Moneda en la que el banco registra los movimientos de esta cuenta. '
             'Es contra esta moneda que Pabilo compara el monto al verificar un '
             'pago, sin importar en que moneda cobre el POS.',
    )
    # Relacion inversa, no un campo propio: el espejo es de solo lectura y un
    # Many2one aqui habria que escribirlo bajo el contexto de sincronizacion.
    # El dato vive donde importa, en el metodo de pago.
    payment_method_ids = fields.One2many(
        'pos.payment.method', 'pabilo_user_bank_id',
        string='Métodos de Pago',
        help='Métodos de pago del Punto de Venta que verifican contra esta cuenta.',
    )
    payment_method_count = fields.Integer(
        string='Nº de Métodos', compute='_compute_payment_method_count')

    _sql_constraints = [
        ('pabilo_id_company_uniq',
         'UNIQUE(pabilo_id, company_id)',
         'Esta cuenta de Pabilo ya está sincronizada en esta compañía.'),
    ]

    @api.depends('name', 'provider', 'account_number')
    def _compute_display_name(self):
        provider_labels = dict(self._fields['provider'].selection)
        for rec in self:
            parts = []
            if rec.provider:
                parts.append(provider_labels.get(rec.provider, rec.provider))
            if rec.name:
                parts.append(rec.name)
            if rec.account_number:
                parts.append(f"({rec.account_number[-4:]})")
            rec.display_name = ' - '.join(parts) if parts else rec.pabilo_id

    @api.depends('provider')
    def _compute_currency_id(self):
        # active_test=False: en una base cuya moneda de compania es el dolar, el
        # bolivar suele quedar desactivado, y aun asi es la moneda del banco.
        currencies = self.env['res.currency'].with_context(active_test=False).sudo()
        cache = {}
        for rec in self:
            names = BANK_CURRENCY_BY_PROVIDER.get(rec.provider, DEFAULT_BANK_CURRENCY_NAMES)
            if names not in cache:
                # Se recorren en orden y gana el primero que exista. Un search
                # con `in` devolveria el alfabeticamente menor, o sea VEF.
                found = currencies.browse()
                for name in names:
                    found = currencies.search([('name', '=', name)], limit=1)
                    if found:
                        break
                cache[names] = found
            rec.currency_id = cache[names]

    def _compute_payment_method_count(self):
        # active_test=False: un metodo archivado sigue contando, porque es el que
        # explica por que el boton no vuelve a crear uno para esta cuenta.
        for rec in self:
            rec.payment_method_count = self.env['pos.payment.method'].with_context(
                active_test=False).search_count([('pabilo_user_bank_id', '=', rec.id)])

    # -- Un metodo de pago (y un diario) por cuenta ------------------------
    #
    # Un solo metodo de pago para varias cuentas descuadra la contabilidad: el
    # cajero elige a que cuenta llego el dinero, pero el asiento va al diario
    # fijo del metodo, asi que un cobro que entro al BDV termina asentado en el
    # de Binance. El dinero se verifica bien y se contabiliza mal.
    #
    # Con un metodo por cuenta, elegir el metodo ya es elegir la cuenta, y las
    # dos cosas dejan de poder separarse.
    #
    # Nada de esto corre solo: lo dispara un boton en Ajustes. Crear diarios es
    # tocar contabilidad, y eso no puede pasar en el cron de madrugada ni a
    # media venta.

    def _pabilo_journal_code(self):
        """Codigo corto y unico para el diario de esta cuenta.

        `account.journal.code` son 5 caracteres y unico por compania
        (`code_company_uniq`), asi que hace falta resolver colisiones: dos
        cuentas distintas pueden terminar en los mismos 4 digitos.
        """
        self.ensure_one()
        sufijo = ''.join(c for c in (self.account_number or '') if c.isalnum())[-4:]
        if not sufijo:
            sufijo = (self.pabilo_id or '')[-4:] or 'BANK'
        base = ('P%s' % sufijo.upper())[:5]

        Journal = self.env['account.journal'].with_context(active_test=False)
        dominio = [('company_id', '=', self.company_id.id)]
        if not Journal.search_count(dominio + [('code', '=', base)]):
            return base
        for i in range(2, 100):
            cola = str(i)
            candidato = base[:5 - len(cola)] + cola
            if not Journal.search_count(dominio + [('code', '=', candidato)]):
                return candidato
        raise UserError(_(
            'No se pudo generar un código de diario libre para la cuenta %s.',
            self.display_name))

    def _pabilo_short_label(self):
        """"Mi cuenta binance (0001)": la descripcion y los ultimos digitos.

        Es como el contable y el cajero reconocen la cuenta. Se evita
        `display_name` porque antepone el proveedor, que a menudo repite la
        descripcion.
        """
        self.ensure_one()
        etiqueta = self.name or self.display_name or self.pabilo_id
        digitos = (self.account_number or '')[-4:]
        return '%s (%s)' % (etiqueta, digitos) if digitos else etiqueta

    def _pabilo_journal_name(self):
        self.ensure_one()
        return ('Pabilo - %s' % self._pabilo_short_label())[:64]

    def _pabilo_ensure_journal(self):
        """Diario de esta cuenta, creandolo si no existe.

        Se crea **sin moneda**, o sea en la de la compania, y no en la del banco.
        Es deliberado: `pos.config._check_payment_method_ids` rechaza un metodo
        de pago cuyo diario tenga una moneda distinta a la del TPV, asi que un
        diario en bolivares no se podria ni agregar a una caja que cobra en
        dolares. Y es lo correcto: `pos.payment.amount` esta en moneda del TPV;
        los bolivares solo se usan para buscar el movimiento en el banco.
        """
        self.ensure_one()
        Journal = self.env['account.journal']
        existente = Journal.search([
            ('company_id', '=', self.company_id.id),
            ('pabilo_user_bank_id', '=', self.id),
        ], limit=1)
        if existente:
            return existente
        return Journal.create({
            # `display_name` no sirve aqui: repite el proveedor, que muchas veces
            # es igual a la descripcion ("Pabilo - BDV Personal - BDV Personal -
            # (4041)"). El contable necesita la descripcion y los ultimos digitos,
            # que es como identifica la cuenta en su extracto.
            'name': self._pabilo_journal_name(),
            'code': self._pabilo_journal_code(),
            'type': 'bank',
            'currency_id': False,
            'company_id': self.company_id.id,
            'pabilo_user_bank_id': self.id,
        })

    def _pabilo_method_name(self):
        """Nombre del metodo: "Pabilo - Mi cuenta binance"."""
        self.ensure_one()
        base = 'Pabilo - %s' % (self.name or self.display_name or self.pabilo_id)
        Method = self.env['pos.payment.method'].with_context(active_test=False)
        if not Method.search_count([('name', '=', base),
                                    ('company_id', '=', self.company_id.id)]):
            return base
        # Dos cuentas con la misma descripcion en Pabilo: se desambigua con los
        # ultimos digitos, que es lo que el cajero reconoce.
        return '%s (%s)' % (base, (self.account_number or self.pabilo_id)[-4:])

    def _pabilo_ensure_payment_method(self):
        """Metodo de pago de esta cuenta. Devuelve (metodo, creado).

        Si la cuenta ya tiene uno **no se toca nada**: asi sobreviven los
        renombres del cliente sin necesidad de ninguna marca, y de paso se
        esquiva `_is_write_forbidden`, que prohibe escribir en un metodo de pago
        con sesiones POS abiertas. Solo se crea, nunca se reescribe.
        """
        self.ensure_one()
        Method = self.env['pos.payment.method'].with_context(active_test=False)
        existente = Method.search([('pabilo_user_bank_id', '=', self.id)], limit=1)
        if existente:
            return existente, False
        metodo = self.env['pos.payment.method'].create({
            'name': self._pabilo_method_name(),
            'use_payment_terminal': 'pabilo',
            'pabilo_user_bank_id': self.id,
            'journal_id': self._pabilo_ensure_journal().id,
            'company_id': self.company_id.id,
        })
        return metodo, True

    @api.model
    def action_create_payment_methods(self):
        """Crea el metodo de pago que falte para cada cuenta de la compania.

        Devuelve (creados, archivados, bloqueados, mensaje). Nunca lanza: lo
        llama un boton de Ajustes y un fallo parcial tiene que poder contarse.
        """
        company = self.env.company
        creados, arreglados, archivados, bloqueados = [], [], [], []

        for cuenta in self.search([('company_id', '=', company.id),
                                   ('is_trashed', '=', False)]):
            try:
                metodo, nuevo = cuenta._pabilo_ensure_payment_method()
            except Exception as e:
                _logger.exception("Pabilo: no se pudo crear el metodo de %s", cuenta.display_name)
                bloqueados.append('%s (%s)' % (cuenta.display_name, e))
                continue
            if nuevo:
                creados.append(metodo.name)
                continue
            # Metodo que ya existia sin diario: sin el, Odoo lo trata como
            # «pagar despues» y el cobro no entra en caja. No es pisarle la
            # configuracion al cliente, es rellenar lo que esta vacio y roto.
            # Es el caso de quien venia de una version anterior y la migracion
            # no pudo arreglar por tener la caja abierta.
            if not metodo.journal_id:
                try:
                    metodo.journal_id = cuenta._pabilo_ensure_journal()
                    arreglados.append(metodo.name)
                except UserError as e:
                    bloqueados.append('%s (%s)' % (metodo.name, e))

        # Cuentas que ya no existen en Pabilo: su metodo se archiva, nunca se
        # borra. Hay `pos.payment` historicos apuntando ahi y borrarlo dejaria
        # ventas viejas sin metodo de pago.
        eliminadas = self.search([('company_id', '=', company.id), ('is_trashed', '=', True)])
        vivos = self.env['pos.payment.method'].search([
            ('pabilo_user_bank_id', 'in', eliminadas.ids)])
        for metodo in vivos:
            try:
                metodo.active = False
                archivados.append(metodo.name)
            except UserError as e:
                # Sesion POS abierta: Odoo prohibe tocar el metodo. Se informa,
                # no se rompe el boton por esto.
                bloqueados.append('%s (%s)' % (metodo.name, e))

        partes = []
        if creados:
            partes.append(_('%s métodos de pago creados: %s',
                            len(creados), ', '.join(creados)))
        if arreglados:
            partes.append(_('%s métodos que no tenían diario ya lo tienen (sin él '
                            'el cobro no entraba en caja): %s',
                            len(arreglados), ', '.join(arreglados)))
        if archivados:
            partes.append(_('%s archivados por cuentas eliminadas en Pabilo: %s',
                            len(archivados), ', '.join(archivados)))
        if bloqueados:
            partes.append(_('%s no se pudieron ajustar: %s',
                            len(bloqueados), '; '.join(bloqueados)))
        if not partes:
            partes.append(_('Todas las cuentas ya tenían su método de pago. '
                            'No hizo falta crear nada.'))

        mensaje = '\n'.join(partes)
        _logger.info("Pabilo: %s", mensaje.replace('\n', ' | '))
        return len(creados) + len(arreglados), len(archivados), len(bloqueados), mensaje

    def action_view_payment_methods(self):
        """Botón inteligente de la ficha de la cuenta."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Métodos de Pago de %s', self.display_name),
            'res_model': 'pos.payment.method',
            'view_mode': 'tree,form',
            'domain': [('pabilo_user_bank_id', '=', self.id)],
            'context': {'active_test': False},
        }

    # -- Solo lectura -----------------------------------------------------
    # La ACL ya niega write/create/unlink a todos los grupos, pero eso lo
    # saltaría cualquier `sudo()`. Estas guardas cierran también esa puerta,
    # de modo que la única forma de tocar el espejo sea la sincronización.

    @api.model
    def _assert_sync_context(self):
        if not self.env.context.get(SYNC_CONTEXT_KEY):
            raise UserError(_(
                "Las cuentas de Pabilo son un espejo de solo lectura.\n\n"
                "Para modificarlas, edítalas en Pabilo y luego pulsa "
                "Ajustes → Pabilo → Sincronizar Cuentas."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        self._assert_sync_context()
        return super().create(vals_list)

    def write(self, vals):
        self._assert_sync_context()
        return super().write(vals)

    def unlink(self):
        self._assert_sync_context()
        return super().unlink()

    # -- Sincronización automática ----------------------------------------
    # Obligar a pulsar un botón para tener las cuentas al día es una fuente
    # silenciosa de errores: si alguien agrega una cuenta en Pabilo y nadie
    # sincroniza, el cajero no la ve y verifica contra la equivocada. Por eso
    # se refresca sola cuando hace falta, y el botón queda solo para forzarla.

    SYNC_MAX_AGE_MINUTES = 60

    @api.model
    def _sync_if_stale(self, max_age_minutes=None):
        """Sincroniza si el espejo está vacío o viejo. Nunca lanza excepción:
        se llama desde el POS, y un fallo de red no puede romper un cobro."""
        company = self.env.company.sudo()
        if not company.pabilo_api_key:
            return False

        max_age = max_age_minutes or self.SYNC_MAX_AGE_MINUTES
        last = company.pabilo_last_sync
        if last and (fields.Datetime.now() - last) < timedelta(minutes=max_age):
            has_any = self.sudo().search_count([('company_id', '=', company.id)])
            if has_any:
                return False

        try:
            ok, message = self.action_sync_banks()
            if not ok:
                _logger.warning("Pabilo auto-sync falló: %s", message)
            return ok
        except Exception:
            _logger.exception("Pabilo auto-sync lanzó excepción")
            return False

    @api.model
    def _fetch_webhook_secret(self):
        """Trae de Pabilo el secreto de firma de webhooks de este usuario.

        No sobrescribe uno ya guardado: si el admin lo puso a mano, manda el
        suyo. Nunca lanza excepción — es un extra de la sincronización, no puede
        tumbarla.
        """
        params = self.env['ir.config_parameter'].sudo()
        if params.get_param('pabilo.webhook_secret'):
            return False
        try:
            ok, secret, message = self.env['pabilo.client'].fetch_webhook_secret()
        except Exception:
            _logger.exception("Pabilo: error obteniendo el secreto del webhook")
            return False
        if not ok:
            _logger.warning("Pabilo: no se pudo obtener el secreto del webhook: %s", message)
            return False
        params.set_param('pabilo.webhook_secret', secret)
        _logger.info("Pabilo: secreto del webhook obtenido y guardado")
        return True

    @api.model
    def _cron_sync_banks(self):
        """Red de seguridad diaria: refresca todas las compañías con appKey."""
        companies = self.env['res.company'].sudo().search([('pabilo_api_key', '!=', False)])
        for company in companies:
            try:
                self.with_company(company).action_sync_banks()
            except Exception:
                _logger.exception("Pabilo cron sync falló para %s", company.name)

    def action_sync_banks(self):
        """Sincroniza las cuentas bancarias de la compañía actual desde Pabilo.

        Devuelve (ok, mensaje): el llamador (Ajustes) muestra éxito o error real.
        """
        company = self.env.company
        if not company.sudo().pabilo_api_key:
            return False, _('Configura primero el API Key de Pabilo.')

        ok, banks_raw, message = self.env['pabilo.client'].get_user_banks()
        if not ok:
            _logger.warning("Pabilo sync error para %s: %s", company.name, message)
            return False, message

        # De paso se trae el secreto con el que Pabilo firma los webhooks de
        # este usuario. El backend lo genera la primera vez que se pide, así que
        # nadie tiene que copiarlo a mano ni compartirlo entre comercios.
        self._fetch_webhook_secret()

        # Espejo escribible: sudo() salta la ACL de solo lectura y el flag de
        # contexto pasa las guardas de create/write/unlink.
        mirror = self.sudo().with_context(**{SYNC_CONTEXT_KEY: True})
        provider_values = dict(self._fields['provider'].selection)

        synced = 0
        seen_ids = []
        for bank in banks_raw:
            bank_id = str(bank.get('id', ''))
            if not bank_id:
                continue
            seen_ids.append(bank_id)

            existing = mirror.search([
                ('pabilo_id', '=', bank_id),
                ('company_id', '=', company.id),
            ], limit=1)

            # Eliminada en Pabilo: no se crea, y si ya existía se marca.
            if bank.get('to_trash', False):
                if existing:
                    existing.write({'is_trashed': True})
                continue

            # Extraer datos de la cuenta
            provider = bank.get('provider', '')
            description = bank.get('description', '')
            account_entries = bank.get('bank_accounts', [])
            account_number = ''
            account_type = ''
            if account_entries and isinstance(account_entries, list):
                account_number = account_entries[0].get('account_number', '')
                account_type = account_entries[0].get('account_type', account_entries[0].get('type', ''))
            supports_pl = bank.get('payment_link', True)

            vals = {
                'name': description or provider,
                'pabilo_id': bank_id,
                'provider': provider if provider in provider_values else False,
                'account_number': account_number,
                'account_type': account_type,
                'supports_payment_link': supports_pl,
                'is_trashed': False,
                'company_id': company.id,
            }

            if existing:
                existing.write(vals)
            else:
                mirror.create(vals)
            synced += 1

        # Lo que Pabilo ya no devuelve se marca eliminado: como el espejo es de
        # solo lectura, nadie puede limpiarlo a mano.
        stale = mirror.search([
            ('company_id', '=', company.id),
            ('pabilo_id', 'not in', seen_ids),
            ('is_trashed', '=', False),
        ])
        if stale:
            stale.write({'is_trashed': True})

        company.sudo().pabilo_last_sync = fields.Datetime.now()

        _logger.info("Pabilo: sincronizadas %d cuentas para %s (%d marcadas eliminadas)",
                     synced, company.name, len(stale))
        return True, _('Se han sincronizado %s cuentas bancarias con Pabilo.', synced)
