from odoo import fields, models


class PabiloPaymentMethodWizard(models.TransientModel):
    _name = 'pabilo.payment.method.wizard'
    _description = 'Asistente para crear un método de pago Pabilo'

    name = fields.Char(string='Nombre', required=True, default='Pago Móvil Pabilo')
    pabilo_user_bank_id = fields.Many2one(
        'pabilo.user.bank',
        string='Cuenta Bancaria Pabilo',
        required=True,
        domain="[('company_id', '=', company_id), ('is_trashed', '=', False)]",
        help='La cuenta bancaria de Pabilo donde se verificarán los pagos recibidos con este método.'
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Diario',
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        help='Opcional. Diario contable donde se registrarán los pagos.'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
        readonly=True,
    )

    def action_confirm(self):
        """Crea el método de pago POS ya configurado para Pabilo y lo abre."""
        self.ensure_one()
        method = self.env['pos.payment.method'].create({
            'name': self.name,
            'use_payment_terminal': 'pabilo',
            'pabilo_user_bank_id': self.pabilo_user_bank_id.id,
            'journal_id': self.journal_id.id or False,
            'company_id': self.company_id.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Método de Pago Pabilo',
            'res_model': 'pos.payment.method',
            'res_id': method.id,
            'view_mode': 'form',
            'target': 'current',
        }
