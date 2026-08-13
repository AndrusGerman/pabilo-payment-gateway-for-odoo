from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # groups: el appKey no debe ser legible por cualquier usuario interno vía RPC.
    # Quien lo lea en código debe usar env.company.sudo().pabilo_api_key.
    pabilo_api_key = fields.Char(string='Pabilo API Key', groups='base.group_system')

    # Cuándo se sincronizaron por última vez las cuentas. Permite refrescarlas
    # solas cuando hacen falta, en vez de obligar a pulsar el botón a mano.
    pabilo_last_sync = fields.Datetime(string='Última sincronización Pabilo', readonly=True)
