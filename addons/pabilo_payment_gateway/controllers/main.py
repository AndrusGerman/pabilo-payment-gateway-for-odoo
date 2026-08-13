import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)

# Tolerancia del timestamp firmado. Sin ella, una petición capturada se puede
# repetir indefinidamente aunque la firma sea legítima.
SIGNATURE_TOLERANCE_SECONDS = 300

# Estados que Pabilo puede mandar. Se valida contra esta lista antes de escribir
# el Selection: un valor inesperado lanzaría ValueError y, como el error se
# traga y se responde 200, Pabilo lo daría por entregado y no reintentaría.
LINK_STATUSES = ('pending', 'active', 'paid', 'failed', 'canceled', 'expired', 'stopped')
FAILED_STATUSES = ('failed', 'canceled', 'expired')


class PabiloWebhookController(http.Controller):

    def _verify_signature(self, raw_body):
        """Comprueba la firma HMAC del webhook.

        Devuelve (ok, motivo). El secreto se configura en Ajustes → Pabilo y
        debe coincidir con WEBHOOK_SIGN_SECRET del backend.
        """
        secret = request.env['ir.config_parameter'].sudo().get_param('pabilo.webhook_secret', '')
        if not secret:
            # Sin secreto no se puede distinguir a Pabilo de cualquier otro que
            # conozca la URL. Se rechaza en vez de aceptar a ciegas: un webhook
            # que marca pagos como cobrados no puede quedar abierto por olvido.
            return False, 'webhook secret not configured'

        timestamp = request.httprequest.headers.get('X-Pabilo-Timestamp', '')
        received = request.httprequest.headers.get('X-Pabilo-Signature', '')
        if not timestamp or not received:
            return False, 'missing signature headers'

        try:
            age = abs(time.time() - int(timestamp))
        except (TypeError, ValueError):
            return False, 'invalid timestamp'
        if age > SIGNATURE_TOLERANCE_SECONDS:
            return False, 'timestamp outside tolerance'

        expected = 'sha256=' + hmac.new(
            secret.encode(), timestamp.encode() + b'.' + raw_body, hashlib.sha256
        ).hexdigest()

        # consteq y no ==: comparar cadenas normalmente filtra la firma byte a
        # byte por diferencia de tiempo.
        if not consteq(expected, received):
            return False, 'signature mismatch'
        return True, ''

    @http.route('/pabilo/webhook', type='http', auth='public', methods=['POST'],
                csrf=False, save_session=False)
    def pabilo_webhook(self, **kwargs):
        """Recibe los webhooks de Pabilo cuando un enlace de pago cambia de estado.

        type='http' y no 'json': con 'json' Odoo espera el sobre JSON-RPC 2.0
        (params/result) y Pabilo postea JSON plano, así que los datos no
        llegarían y la respuesta sería un sobre que Pabilo no entiende.

        Los errores devuelven 4xx/5xx a propósito: Pabilo registra como fallida
        cualquier respuesta fuera de 2xx, y así el fallo queda visible en su
        panel en lugar de perderse.
        """
        raw_body = request.httprequest.get_data()

        ok, reason = self._verify_signature(raw_body)
        if not ok:
            _logger.warning("Pabilo webhook rechazado (%s) desde %s",
                            reason, request.httprequest.remote_addr)
            return request.make_json_response({'ok': False, 'error': reason}, status=401)

        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return request.make_json_response({'ok': False, 'error': 'invalid json'}, status=400)

        payment_link_id = payload.get('payment_link_id') or ''
        status = payload.get('status') or ''
        _logger.info("Pabilo webhook: link=%s status=%s", payment_link_id, status)

        if not payment_link_id:
            return request.make_json_response(
                {'ok': False, 'error': 'missing payment_link_id'}, status=400)

        if status not in LINK_STATUSES:
            return request.make_json_response(
                {'ok': False, 'error': 'unknown status: %s' % status}, status=400)

        tx = request.env['payment.transaction'].sudo().search(
            [('pabilo_payment_link_id', '=', payment_link_id)], limit=1)
        if not tx:
            # 404 y no 200: si se responde OK, Pabilo lo da por entregado y la
            # transacción nunca se entera de que se pagó.
            _logger.warning("Pabilo webhook: sin transacción para el enlace %s", payment_link_id)
            return request.make_json_response(
                {'ok': False, 'error': 'transaction_not_found'}, status=404)

        try:
            tx.pabilo_payment_link_status = status

            if status == 'paid':
                payment_data = payload.get('user_bank_payment') or {}
                tx.write({
                    'pabilo_status': 'verified',
                    'pabilo_payment_id': payment_data.get('id', ''),
                    'pabilo_reference': payment_data.get('bank_reference_id', ''),
                })
                if tx.state != 'done':
                    tx._set_done()
                _logger.info("Pabilo webhook: transacción %s marcada como pagada", tx.reference)

            elif status in FAILED_STATUSES:
                tx.pabilo_status = 'failed'
                if tx.state not in ('done', 'cancel'):
                    tx._set_canceled()
                _logger.info("Pabilo webhook: transacción %s marcada como %s", tx.reference, status)

        except Exception as e:
            # 500 para que Pabilo lo registre como entrega fallida y quede
            # rastro; antes se respondía 200 y el fallo desaparecía.
            _logger.exception("Pabilo webhook: error procesando el enlace %s", payment_link_id)
            return request.make_json_response({'ok': False, 'error': str(e)}, status=500)

        return request.make_json_response({'ok': True})
