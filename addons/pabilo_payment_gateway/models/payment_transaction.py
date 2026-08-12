import logging

import requests

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    pabilo_reference = fields.Char(
        string='Referencia Bancaria',
        help='Número de referencia del pago (Pago Móvil, Binance, Transferencia)',
    )
    pabilo_user_bank_id = fields.Many2one(
        'pabilo.user.bank',
        string='Cuenta Pabilo',
        help='Cuenta bancaria de Pabilo donde se recibió el pago',
    )
    pabilo_payment_id = fields.Char(string='ID Pago Pabilo', readonly=True)
    pabilo_movement_type = fields.Selection([
        ('GENERIC', 'Genérico (cualquier tipo)'),
        ('MOVIL_PAY', 'Pago Móvil'),
        ('TRANSFER', 'Transferencia Bancaria'),
        ('C2P', 'Cuenta a Persona (C2P)'),
    ], string='Tipo de Movimiento', default='GENERIC')
    pabilo_status = fields.Selection([
        ('pending', 'Pendiente'),
        ('verified', 'Verificado'),
        ('failed', 'Fallido'),
    ], string='Estado Pabilo', default='pending')
    pabilo_is_new = fields.Boolean(string='Es Nuevo', readonly=True)
    pabilo_credit_cost = fields.Integer(string='Créditos Consumidos', readonly=True)

    # --- Payment Link fields ---
    pabilo_payment_link_id = fields.Char(string='ID Enlace Pabilo', readonly=True)
    pabilo_payment_link_url = fields.Char(string='URL Enlace de Pago', readonly=True)
    pabilo_payment_link_status = fields.Selection([
        ('pending', 'Pendiente'),
        ('active', 'Activo'),
        ('paid', 'Pagado'),
        ('failed', 'Fallido'),
        ('canceled', 'Cancelado'),
        ('expired', 'Expirado'),
        ('stopped', 'Detenido'),
    ], string='Estado Enlace', readonly=True)

    def _get_pabilo_api_key(self):
        """Obtiene el API key de Pabilo: primero del proveedor, luego de la compañía."""
        self.ensure_one()
        api_key = self.env['pabilo.client']._api_key(provider=self.provider_id)
        if not api_key:
            raise UserError('El API Key de Pabilo no está configurado.')
        return api_key

    def _pabilo_headers(self, api_key):
        return {
            'Content-Type': 'application/json',
            'appKey': api_key,
        }

    def _pabilo_base_url(self):
        return self.env['pabilo.client']._base_url()

    # ==========================================
    # Verificación directa (betaserio) para POS
    # ==========================================
    def action_verify_pabilo_payment(self):
        """Verifica un pago contra la API de Pabilo usando betaserio."""
        for record in self:
            if not record.pabilo_reference:
                raise UserError('Ingresa la referencia de pago.')
            if not record.pabilo_user_bank_id:
                raise UserError('Selecciona una Cuenta Pabilo.')

            result = self.env['pabilo.client'].verify_payment(
                record.pabilo_user_bank_id,
                record.pabilo_reference,
                record.amount,
                movement_type=record.pabilo_movement_type or 'GENERIC',
                provider=record.provider_id,
            )

            if result['verified']:
                record.write({
                    'pabilo_status': 'verified',
                    'pabilo_payment_id': result['payment_id'],
                    'pabilo_is_new': result['is_new'],
                    'pabilo_credit_cost': result['credit_cost'],
                })
                record._set_done()
            else:
                raise UserError(f"Pabilo: {result['message']}")

    # ==========================================
    # Enlace de pago (Payment Link)
    # ==========================================
    def action_create_pabilo_payment_link(self):
        """Crea un enlace de pago en Pabilo para esta transacción."""
        for record in self:
            if not record.pabilo_user_bank_id:
                raise UserError('Selecciona una Cuenta Pabilo para el enlace de pago.')

            api_key = record._get_pabilo_api_key()

            # Mapear la moneda de Odoo a la de Pabilo
            currency_map = {
                'VES': 'VEF', 'VEF': 'VEF',
                'USD': 'USD',
                'EUR': 'EUR',
            }
            odoo_currency = record.currency_id.name if record.currency_id else 'VEF'
            pabilo_currency = currency_map.get(odoo_currency, 'VEF')

            # Construir la URL del webhook
            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
            webhook_url = f"{base_url}/pabilo/webhook" if base_url else ''

            payload = {
                'amount': record.amount,
                'description': record.reference or f'Pago Odoo #{record.id}',
                'user_bank_id': record.pabilo_user_bank_id.pabilo_id,
                'currency': pabilo_currency,
                'name': record.reference or '',
            }
            if webhook_url:
                payload['webhook_url'] = webhook_url
            # Metadatos para vincular de vuelta
            payload['metadata'] = {
                'odoo_transaction_id': str(record.id),
                'odoo_reference': record.reference or '',
            }

            try:
                resp = requests.post(
                    f'{record._pabilo_base_url()}/v1/paymentlink',
                    json=payload,
                    headers=record._pabilo_headers(api_key),
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    body = resp.json()
                    # Normalizar — puede venir en 'paymentlink', 'data.payment_link', 'data', o raíz
                    link = (
                        body.get('paymentlink')
                        or (body.get('data', {}).get('payment_link') if isinstance(body.get('data'), dict) else None)
                        or body.get('data')
                        or body
                    )
                    record.write({
                        'pabilo_payment_link_id': link.get('id', ''),
                        'pabilo_payment_link_url': link.get('url', ''),
                        'pabilo_payment_link_status': link.get('status', 'pending'),
                    })
                    return {
                        'type': 'ir.actions.act_url',
                        'url': link.get('url', ''),
                        'target': 'new',
                    }
                else:
                    raise UserError(f"Error creando enlace ({resp.status_code}): {resp.text}")

            except requests.exceptions.RequestException as e:
                _logger.error("Pabilo payment link error: %s", e)
                raise UserError("No se pudo conectar con Pabilo.")

    def action_check_pabilo_payment_link(self):
        """Consulta el estado de un enlace de pago ya creado."""
        for record in self:
            if not record.pabilo_payment_link_id:
                raise UserError('No hay enlace de pago asociado.')

            api_key = record._get_pabilo_api_key()
            url = f'{record._pabilo_base_url()}/paymentlink/{record.pabilo_payment_link_id}/info'

            try:
                resp = requests.get(url, headers=record._pabilo_headers(api_key), timeout=15)
                if resp.status_code == 200:
                    body = resp.json()
                    link = (
                        body.get('paymentlink')
                        or (body.get('data', {}).get('payment_link') if isinstance(body.get('data'), dict) else None)
                        or body.get('data')
                        or body
                    )
                    status = link.get('status', '')
                    record.pabilo_payment_link_status = status

                    if status == 'paid' and record.pabilo_status != 'verified':
                        record.pabilo_status = 'verified'
                        record._set_done()

                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Estado del Enlace',
                            'message': f'Estado actual: {status}',
                            'sticky': False,
                            'type': 'info' if status != 'paid' else 'success',
                        }
                    }
                else:
                    raise UserError(f"Error consultando enlace: {resp.text}")
            except requests.exceptions.RequestException as e:
                _logger.error("Pabilo link check error: %s", e)
                raise UserError("No se pudo conectar con Pabilo.")
