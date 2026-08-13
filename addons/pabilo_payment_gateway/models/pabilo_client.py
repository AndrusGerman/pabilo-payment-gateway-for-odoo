import logging

import requests

from odoo import _, _lt, api, models

_logger = logging.getLogger(__name__)

PABILO_DEFAULT_API_URL = 'https://api.pabilo.app'

# Códigos de error del backend (internal/core/domain/errors.go) -> mensaje para el
# cajero. Con _lt (lazy) y no _(): el diccionario se evalúa al importar el módulo,
# cuando todavía no hay usuario ni idioma. La traducción se resuelve al usarlo.
PABILO_ERROR_MESSAGES = {
    'PAYMENT_NOT_FOUND': _lt('El pago aún no aparece en el banco.'),
    'PAYMENT_AMOUNT_NOT_VALID': _lt('La referencia existe pero el monto no coincide.'),
    'NOT_ENOUGH_CREDITS': _lt('Sin créditos en Pabilo.'),
    'USER_BANK_IS_DISABLED': _lt('La cuenta bancaria de Pabilo está deshabilitada.'),
    'USER_BANCK_BLOCKED': _lt('La cuenta bancaria está bloqueada por el banco; actualiza las credenciales en Pabilo.'),
    'MOVEMENT_TYPE_REQUIRED': _lt('Esta cuenta requiere elegir tipo de movimiento.'),
    'IS_NOT_POSITIVE_PAYMENT': _lt('El movimiento encontrado no es un abono.'),
    'UNAUTHORIZED': _lt('API Key de Pabilo inválido.'),
    'PLAN_IS_NOT_ACTIVE': _lt('La cuenta de Pabilo no tiene un plan activo.'),
    'USER_IS_NOT_ACTIVE': _lt('El usuario de Pabilo no está activo.'),
    'CLIENT_IS_NOT_ACTIVE': _lt('El cliente de Pabilo no está activo.'),
    'REQUEST_LIMIT_REACHED': _lt('Límite de consultas del plan de Pabilo alcanzado.'),
}

# Ningún error de dominio se reintenta. PAYMENT_NOT_FOUND parecía la excepción,
# pero no lo es: el backend ya consultó el banco antes de responderlo, así que
# volver a preguntar da lo mismo. Es un dato firme, igual que un monto que no
# coincide o una referencia ya usada: se muestra y se corta.
#
# Lo que sí puede tardar es la consulta al banco, y para eso está el timeout
# largo de verify_payment en vez de varios intentos cortos.
VERIFY_TIMEOUT_SECONDS = 110


class PabiloClient(models.AbstractModel):
    """Cliente HTTP unificado de la API de Pabilo.

    Concentra URL base, header appKey, parseo de respuestas y normalización de
    errores, para que el POS y payment.transaction hablen con el mismo contrato.
    """
    _name = 'pabilo.client'
    _description = 'Cliente HTTP de la API de Pabilo'

    @api.model
    def _base_url(self):
        return (self.env['ir.config_parameter'].sudo()
                .get_param('pabilo.api_url', PABILO_DEFAULT_API_URL)).rstrip('/')

    @api.model
    def _api_key(self, provider=None):
        """Cascada: provider.pabilo_api_key -> compañía. sudo() porque ambos
        campos están restringidos a base.group_system y el cajero no es admin."""
        if provider and provider.code == 'pabilo':
            provider_key = provider.sudo().pabilo_api_key
            if provider_key:
                return provider_key
        return self.env.company.sudo().pabilo_api_key or ''

    @api.model
    def _request(self, method, path, payload=None, timeout=20, provider=None):
        """Ejecuta una llamada a la API. Devuelve (http_status, body_dict).

        Ante error de red devuelve (None, {'error': 'CONNECTION_ERROR', ...}).
        resp.json() va envuelto en try/except ValueError: JSONDecodeError NO es
        RequestException, y un 502 con HTML no debe reventar con un 500.
        """
        api_key = self._api_key(provider=provider)
        if not api_key:
            return None, {
                'error': 'NO_API_KEY',
                'message': _('El API Key de Pabilo no está configurado (Ajustes → Pabilo).'),
            }
        url = f'{self._base_url()}{path}'
        headers = {'Content-Type': 'application/json', 'appKey': api_key}
        try:
            resp = requests.request(method, url, json=payload, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            _logger.error("Pabilo connection error %s %s: %s", method, url, e)
            return None, {
                'error': 'CONNECTION_ERROR',
                'message': _('No se pudo conectar con Pabilo. Verifique la URL base y la red.'),
            }
        try:
            body = resp.json()
            if not isinstance(body, dict):
                body = {'data': body}
        except ValueError:
            _logger.warning("Pabilo non-JSON response %s %s -> %s", method, url, resp.status_code)
            body = {'error': 'INVALID_RESPONSE', 'message': resp.text[:300]}
        return resp.status_code, body

    @api.model
    def verify_payment(self, user_bank, reference, amount, movement_type='GENERIC',
                       provider=None, source_name=''):
        """Verifica un pago vía betaserio.

        Devuelve un dict normalizado (nunca lanza excepciones de dominio):
        {'verified': bool, 'status': str, 'payment_id': str, 'is_new': bool,
         'credit_cost': float, 'error_code': str, 'message': str}
        """
        result = {
            'verified': False,
            'status': '',
            'payment_id': '',
            'is_new': False,
            'credit_cost': 0.0,
            'error_code': '',
            'message': '',
        }
        payload = {'bank_reference': reference, 'amount': amount}
        if movement_type:
            payload['movement_type'] = movement_type
        # Identifica la caja/cajero que cobró. Con varias cajas contra la misma
        # cuenta, sin esto los pagos son indistinguibles en Pabilo.
        if source_name:
            payload['source_name'] = source_name

        # Timeout largo y una sola llamada: la consulta al banco puede tardar,
        # pero la respuesta es firme, así que reintentar no aporta nada.
        http_status, body = self._request(
            'POST', f'/userbankpayment/{user_bank.pabilo_id}/betaserio',
            payload=payload, provider=provider, timeout=VERIFY_TIMEOUT_SECONDS,
        )

        if http_status == 200:
            data = body.get('data') or {}
            payment = data.get('user_bank_payment') or {}
            result.update({
                'status': payment.get('status', ''),
                'payment_id': payment.get('id', ''),
                'is_new': bool(data.get('is_new', False)),
                'credit_cost': data.get('credit_cost', 0.0),
            })
            result['verified'] = result['status'] == 'paid'
            if not result['verified']:
                result['message'] = _('Pago no verificado. Estado: %s',
                                      result['status'] or _('desconocido'))
            return result

        # Error: el backend responde {"message": ..., "error": "CODE"} (middleware/error.go)
        error_code = body.get('error') or ('HTTP_%s' % http_status)
        result['error_code'] = error_code
        # str() obligatorio: los valores del mapa son _lt (perezosos) y este dict
        # viaja por JSON-RPC hasta el POS, que no sabe serializarlos.
        result['message'] = str(
            PABILO_ERROR_MESSAGES.get(error_code)
            or body.get('message')
            or _('Error desconocido de Pabilo.')
        )
        result['status'] = 'failed'
        _logger.info("Pabilo verify failed ref=%s: %s %s", reference, error_code, result['message'])
        return result

    @api.model
    def fetch_webhook_secret(self, provider=None):
        """GET /me/webhook-secret. Devuelve (ok, secreto, mensaje_error).

        El secreto es de este usuario de Pabilo, no global: con uno compartido
        entre todos los clientes, cualquiera podría firmar webhooks a nombre de
        otro comercio. El backend lo genera la primera vez que se pide, así que
        no hay que copiar nada a mano.
        """
        http_status, body = self._request('GET', '/me/webhook-secret', provider=provider)
        if http_status == 200:
            secret = body.get('webhook_secret') or ''
            if not secret:
                return False, '', _('Pabilo devolvió un secreto de webhook vacío.')
            return True, secret, ''
        message = str(
            PABILO_ERROR_MESSAGES.get(body.get('error', ''))
            or body.get('message')
            or _('Error obteniendo el secreto del webhook.')
        )
        return False, '', message

    @api.model
    def get_user_banks(self, provider=None):
        """GET /me/usersbank. Devuelve (ok, lista_de_bancos, mensaje_error)."""
        http_status, body = self._request('GET', '/me/usersbank', provider=provider)
        if http_status == 200:
            banks = body.get('user_banks')
            if banks is None:
                banks = body.get('data') or []
            return True, banks, ''
        error_code = body.get('error', '')
        message = str(
            PABILO_ERROR_MESSAGES.get(error_code)
            or body.get('message')
            or _('Error consultando las cuentas de Pabilo.')
        )
        return False, [], message
