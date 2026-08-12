import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PabiloWebhookController(http.Controller):

    @http.route('/pabilo/webhook', type='json', auth='none', methods=['POST'], csrf=False)
    def pabilo_webhook(self, **kwargs):
        """
        Recibe los webhooks de Pabilo cuando un enlace de pago cambia de estado.
        Payload esperado (PaymentLinkWebhookPayload):
        {
            "id": "...",
            "payment_link_id": "...",
            "status": "paid",
            "user_bank_payment": {...},
            "credit_balance": 99,
            "metadata": [{"key": "odoo_transaction_id", "value": "42"}, ...]
        }
        """
        try:
            payload = request.jsonrequest
            _logger.info("Pabilo webhook received: %s", json.dumps(payload, default=str)[:500])

            payment_link_id = payload.get('payment_link_id', '')
            status = payload.get('status', '')

            if not payment_link_id:
                _logger.warning("Pabilo webhook: no payment_link_id")
                return {'ok': False, 'error': 'missing payment_link_id'}

            # Buscar la transacción por el ID del payment link
            tx = request.env['payment.transaction'].sudo().search([
                ('pabilo_payment_link_id', '=', payment_link_id)
            ], limit=1)

            if not tx:
                # Intentar buscar por metadata
                metadata = payload.get('metadata', [])
                odoo_tx_id = None
                for entry in metadata:
                    if isinstance(entry, dict) and entry.get('key') == 'odoo_transaction_id':
                        odoo_tx_id = entry.get('value')
                        break
                if odoo_tx_id:
                    tx = request.env['payment.transaction'].sudo().browse(int(odoo_tx_id))
                    if not tx.exists():
                        tx = None

            if not tx:
                _logger.warning("Pabilo webhook: no transaction found for link %s", payment_link_id)
                return {'ok': False, 'error': 'transaction_not_found'}

            # Actualizar estado
            tx.pabilo_payment_link_status = status

            if status == 'paid':
                payment_data = payload.get('user_bank_payment', {})
                tx.write({
                    'pabilo_status': 'verified',
                    'pabilo_payment_id': payment_data.get('id', ''),
                    'pabilo_reference': payment_data.get('bank_reference_id', ''),
                })
                if tx.state != 'done':
                    tx._set_done()
                _logger.info("Pabilo webhook: transaction %s marked as paid", tx.reference)

            elif status in ('failed', 'canceled', 'expired'):
                tx.pabilo_status = 'failed'
                if tx.state not in ('done', 'cancel'):
                    tx._set_canceled()
                _logger.info("Pabilo webhook: transaction %s marked as %s", tx.reference, status)

            return {'ok': True}

        except Exception as e:
            _logger.error("Pabilo webhook error: %s", e)
            return {'ok': False, 'error': str(e)}
