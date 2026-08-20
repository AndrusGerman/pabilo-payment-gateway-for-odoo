{
    'name': 'Pabilo Payment Gateway for Odoo',
    'version': '16.0.2.4.1',
    'summary': 'Verifica pagos móviles, transferencias y Binance en el POS con Pabilo',
    'description': """
Pabilo Payment Gateway
======================

Verificación automática de pagos venezolanos dentro de Odoo.

En el Punto de Venta el cajero teclea los últimos 6 dígitos de la referencia,
confirma la fecha (ya rellenada con hoy) y Odoo verifica el cobro contra el
banco sin salir de la pantalla de pago.

Funcionalidades
---------------
* Método de pago para el POS que verifica contra la API de Pabilo.
* Pago móvil, transferencias y Binance con el mismo método.
* Selección de la cuenta bancaria destino cuando hay varias.
* Fecha del pago prellenada con hoy, para no teclearla en el caso normal.
* Rechazo de referencias ya usadas, para no cobrar dos veces el mismo pago.
* Multi-moneda: el monto se convierte a la moneda del banco antes de verificar,
  con las tasas nativas de Odoo.
* El cajero elige como se valida: la tasa de Odoo, otra tasa o el monto exacto.
* Un metodo de pago y un diario por cuenta bancaria, creados desde Ajustes.
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
    # Se acredita el aporte real de cada quien. La autoria del codigo la lleva el
    # historial de commits; esta clave la muestra el Apps Store en la ficha.
    'contributors': [
        'AndrusGerman <andrusdiazaleman@gmail.com>',  # autor y mantenedor
        'dasilvacsv <dasilva.csv@gmail.com>',  # revision de codigo y QA
    ],
    'website': 'https://pabilo.app',
    'support': 'contacto@pabilo.app',
    'license': 'OPL-1',
    'price': 25.00,
    'currency': 'USD',
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
        'point_of_sale.assets': [
            'pabilo_payment_gateway/static/src/js/payment_pabilo.js',
            'pabilo_payment_gateway/static/src/js/models.js',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
