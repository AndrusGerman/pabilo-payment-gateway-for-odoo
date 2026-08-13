/** @odoo-module */

import { register_payment_method } from "@point_of_sale/app/store/pos_store";
import { Payment } from "@point_of_sale/app/store/models";
import { PaymentPabilo } from "@pabilo_payment_gateway/js/payment_pabilo";
import { patch } from "@web/core/utils/patch";

// Sin este registro, payment_method.payment_terminal queda undefined y el POS
// trata el método como pago manual: send_payment_request jamás se ejecuta.
register_payment_method("pabilo", PaymentPabilo);

// Serializa la referencia verificada para que llegue al servidor
// (pos.order._payment_fields la persiste en pos.payment).
patch(Payment.prototype, {
    setup() {
        super.setup(...arguments);
        this.pabilo_reference = this.pabilo_reference || "";
        this.pabilo_payment_id = this.pabilo_payment_id || "";
        this.pabilo_is_new = this.pabilo_is_new || false;
    },
    //@override
    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.pabilo_reference = json.pabilo_reference || "";
        this.pabilo_payment_id = json.pabilo_payment_id || "";
        this.pabilo_is_new = json.pabilo_is_new || false;
    },
    //@override
    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        if (json) {
            json.pabilo_reference = this.pabilo_reference || "";
            json.pabilo_payment_id = this.pabilo_payment_id || "";
            json.pabilo_is_new = this.pabilo_is_new || false;
        }
        return json;
    },
});
