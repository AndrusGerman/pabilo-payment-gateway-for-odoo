env['ir.config_parameter'].sudo().set_param('pabilo.webhook_secret', 'secreto-de-prueba')
prov = env['payment.provider'].search([('code', '=', 'pabilo')], limit=1)
vals = {
    'provider_id': prov.id, 'reference': 'WH-TEST-1', 'amount': 25.0,
    'currency_id': env.company.currency_id.id,
    'partner_id': env.ref('base.partner_admin').id,
}
if 'payment_method_id' in env['payment.transaction']._fields:
    PM = env['payment.method']
    pm = PM.search([], limit=1) or PM.create({'name': 'Pabilo', 'code': 'pabilo'})
    vals['payment_method_id'] = pm.id
tx = env['payment.transaction'].sudo().create(vals)
tx.pabilo_payment_link_id = 'link-abc-123'
env.cr.commit()
print("tx:", tx.id, tx.reference, "| estado:", tx.state)
print("cron:", bool(env.ref('pabilo_payment_gateway.ir_cron_pabilo_sync_banks', raise_if_not_found=False)))
print("menu asistente:", bool(env.ref('pabilo_payment_gateway.menu_pabilo_payment_method', raise_if_not_found=False)))
print("secreto webhook configurado:", bool(env['ir.config_parameter'].sudo().get_param('pabilo.webhook_secret')))
