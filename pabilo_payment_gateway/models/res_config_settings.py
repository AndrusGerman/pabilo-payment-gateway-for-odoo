from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pabilo_api_key = fields.Char(
        related='company_id.pabilo_api_key',
        string='Pabilo API Key',
        readonly=False,
        help="Introduce tu appKey de Pabilo para verificar pagos"
    )
    pabilo_api_url = fields.Char(
        string='URL Base de Pabilo',
        config_parameter='pabilo.api_url',
        help="URL del backend de Pabilo. En producción, https://api.pabilo.app; "
             "solo hace falta cambiarla si Pabilo te indica otro servidor."
    )

    pabilo_webhook_secret = fields.Char(
        string='Secreto del Webhook',
        config_parameter='pabilo.webhook_secret',
        help="Se obtiene solo de Pabilo al sincronizar; es propio de tu cuenta, "
             "no compartido con otros comercios. Con él se verifica la firma "
             "HMAC de cada webhook; sin secreto, los webhooks se rechazan "
             "(cualquiera que conozca la URL podría dar un pago por cobrado)."
    )

    def action_pabilo_sync_banks(self):
        # Guardar la configuración antes de sincronizar (el appKey recién tipeado
        # aún no está persistido).
        self.ensure_one()
        self.execute()
        ok, message = self.env['pabilo.user.bank'].action_sync_banks()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sincronización Completa') if ok else _('Error de Sincronización'),
                'message': message,
                'sticky': not ok,
                'type': 'success' if ok else 'warning',
            }
        }

    def action_pabilo_create_payment_methods(self):
        """Crea un método de pago del POS por cada cuenta de Pabilo.

        Va detrás de una sincronización, para no crear métodos a partir de un
        espejo viejo. Es explícito y no automático a propósito: crear diarios es
        tocar contabilidad, y eso no debe pasar en un cron ni a media venta.
        """
        self.ensure_one()
        self.execute()
        ok, sync_message = self.env['pabilo.user.bank'].action_sync_banks()
        if not ok:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No se pudieron crear los métodos'),
                    'message': _('Primero falló la sincronización: %s', sync_message),
                    'sticky': True,
                    'type': 'warning',
                }
            }

        creados, archivados, bloqueados, message = (
            self.env['pabilo.user.bank'].action_create_payment_methods())
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Métodos de Pago Pabilo'),
                'message': message,
                # Si algo quedó bloqueado el aviso se queda fijo: es lo único
                # que exige una acción del administrador.
                'sticky': bool(bloqueados),
                'type': 'warning' if bloqueados else 'success',
            }
        }
