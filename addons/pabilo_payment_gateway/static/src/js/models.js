odoo.define('pabilo_payment_gateway.models', function (require) {
    'use strict';

    const { register_payment_method, Payment } = require('point_of_sale.models');
    const Registries = require('point_of_sale.Registries');

    // Sin este registro, payment_method.payment_terminal queda undefined y el POS
    // trata el método como pago manual: send_payment_request jamás se ejecuta.
    register_payment_method('pabilo', require('pabilo_payment_gateway.payment'));

    // Serializa la referencia verificada para que llegue al servidor
    // (pos.order._payment_fields la persiste en pos.payment).
    Registries.Model.extend(Payment, (Payment) =>
        class extends Payment {
            constructor(obj, options) {
                super(obj, options);
                if (!options.json) {
                    this.pabilo_reference = '';
                    this.pabilo_payment_id = '';
                    this.pabilo_is_new = false;
                }
            }
            init_from_JSON(json) {
                super.init_from_JSON(json);
                this.pabilo_reference = json.pabilo_reference || '';
                this.pabilo_payment_id = json.pabilo_payment_id || '';
                this.pabilo_is_new = json.pabilo_is_new || false;
            }
            export_as_JSON() {
                const json = super.export_as_JSON();
                json.pabilo_reference = this.pabilo_reference || '';
                json.pabilo_payment_id = this.pabilo_payment_id || '';
                json.pabilo_is_new = this.pabilo_is_new || false;
                return json;
            }
        }
    );
});
