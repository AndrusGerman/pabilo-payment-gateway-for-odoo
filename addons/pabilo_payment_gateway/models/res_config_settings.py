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
        help="URL del backend de Pabilo. Para un backend local con Odoo en Docker: "
             "http://host.docker.internal:3349 (127.0.0.1 apuntaría al propio contenedor)."
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
