/** @odoo-module */

import { register_payment_method } from "@point_of_sale/app/store/pos_store";
import { Order, Payment } from "@point_of_sale/app/store/models";
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

/**
 * Evita que un método de pago que ya no existe deje el POS sin abrir.
 *
 * El POS guarda los pedidos sin pagar en el navegador y los restaura al
 * arrancar. `Payment.init_from_JSON` hace, sin comprobar nada:
 *
 *     this.payment_method = this.pos.payment_methods_by_id[json.payment_method_id];
 *     this.name = this.payment_method.name;
 *
 * Si el método ya no está —lo borraron, lo archivaron, o lo quitaron de esta
 * caja— eso es `undefined.name` y la sesión **no abre**: pantalla roja y el
 * cajero no puede cobrar. El constructor sí tiene esa guarda («Please configure
 * a payment method in your POS»); la ruta de restauración no.
 *
 * Con un método de pago por cuenta bancaria, ese conjunto cambia más que antes:
 * se crean, se renombran y se archivan cuando la cuenta desaparece de Pabilo.
 * Que un cambio de configuración pueda dejar una caja sin abrir no es aceptable,
 * así que se descarta la línea huérfana y el pedido se restaura sin ella: queda
 * con su saldo pendiente, que es recuperable y evidente.
 *
 * La guarda es genérica, no solo para Pabilo, porque el método que falta ya no
 * existe y no hay forma de saber de quién era.
 */
patch(Order.prototype, {
    //@override
    init_from_JSON(json) {
        const lineas = json.statement_ids || [];
        const vivas = lineas.filter((linea) => {
            const pago = linea && linea[2];
            if (!pago) {
                return false;
            }
            if (this.pos.payment_methods_by_id[pago.payment_method_id]) {
                return true;
            }
            console.warn(
                "[pabilo] pedido %s: se descarta un pago de %s porque su método " +
                    "de pago (id %s) ya no está disponible en esta caja. " +
                    "El pedido se abre con ese monto pendiente.",
                json.name,
                pago.amount,
                pago.payment_method_id
            );
            return false;
        });
        if (vivas.length !== lineas.length) {
            json = Object.assign({}, json, { statement_ids: vivas });
        }
        super.init_from_JSON(json);
    },
});
