import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PabiloUserBank(models.Model):
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

    def action_sync_banks(self):
        """Sincroniza las cuentas bancarias de la compañía actual desde Pabilo.

        Devuelve (ok, mensaje): el llamador (Ajustes) muestra éxito o error real.
        """
        company = self.env.company
        if not company.sudo().pabilo_api_key:
            return False, 'Configura primero el API Key de Pabilo.'

        ok, banks_raw, message = self.env['pabilo.client'].get_user_banks()
        if not ok:
            _logger.warning("Pabilo sync error para %s: %s", company.name, message)
            return False, message

        synced = 0
        for bank in banks_raw:
            bank_id = str(bank.get('id', ''))
            if not bank_id:
                continue
            # Saltar cuentas eliminadas
            if bank.get('to_trash', False):
                continue

            existing = self.search([
                ('pabilo_id', '=', bank_id),
                ('company_id', '=', company.id),
            ], limit=1)

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
                'provider': provider if provider in dict(self._fields['provider'].selection) else False,
                'account_number': account_number,
                'account_type': account_type,
                'supports_payment_link': supports_pl,
                'is_trashed': False,
                'company_id': company.id,
            }

            if existing:
                existing.write(vals)
            else:
                self.create(vals)
            synced += 1

        _logger.info("Pabilo: sincronizadas %d cuentas bancarias para %s", synced, company.name)
        return True, f'Se han sincronizado {synced} cuentas bancarias con Pabilo.'
