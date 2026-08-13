{
    'name': 'Pabilo Payment Gateway for Odoo',
    'version': '17.0.2.0.0',
    'summary': 'Verifica pagos móviles, transferencias y Binance en el POS con Pabilo',
    'description': """
Pabilo Payment Gateway
======================

Verificación automática de pagos venezolanos dentro de Odoo.

En el Punto de Venta el cajero teclea los últimos 6 dígitos de la referencia y
Odoo confirma el cobro contra el banco en segundos, sin salir de la pantalla de
pago. Si el pago todavía no ha llegado al banco, reintenta solo durante 30
segundos en lugar de fallar.

Funcionalidades
---------------
* Método de pago para el POS que verifica contra la API de Pabilo.
* Pago móvil, transferencias y Binance con el mismo método.
* Selección de la cuenta bancaria destino cuando hay varias.
* Rechazo de referencias ya usadas, para no cobrar dos veces el mismo pago.
* Referencia e ID de Pabilo guardados en el pago del POS, para auditoría.
* Verificación manual y enlaces de pago desde las transacciones de pago.
* Cuentas bancarias sincronizadas desde Pabilo en modo solo lectura.

Requisitos
----------
Una cuenta en https://pabilo.app con su API Key (appKey).
    """,
    'category': 'Accounting/Payment Providers',
    'author': 'AndrusCodex',
    'maintainer': 'AndrusCodex',
    'website': 'https://pabilo.app',
    # TODO antes de publicar: correo de soporte real que se mostrará en la ficha.
    # 'support': 'soporte@pabilo.app',
    'license': 'LGPL-3',
    'depends': ['account', 'payment', 'point_of_sale'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'data/payment_provider_data.xml',
        'views/res_config_settings_views.xml',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',
        'views/pos_payment_method_views.xml',
        'views/pabilo_payment_method_wizard_views.xml',
        'views/pabilo_user_bank_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pabilo_payment_gateway/static/src/js/payment_pabilo.js',
            'pabilo_payment_gateway/static/src/js/models.js',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
