odoo.define('pabilo_payment_gateway.payment', function (require) {
    'use strict';

    const { Gui } = require('point_of_sale.Gui');
    const { _t } = require('web.core');
    const rpc = require('web.rpc');
    const PaymentInterface = require('point_of_sale.PaymentInterface');

    // Un pago móvil tarda segundos en aparecer en el banco: se reintenta solo
    // mientras el backend responda PAYMENT_NOT_FOUND. Cualquier otro error corta.
    const MAX_ATTEMPTS = 10;
    const RETRY_DELAY_MS = 3000;
    const RETRYABLE_ERROR = 'PAYMENT_NOT_FOUND';

    const PabiloPayment = PaymentInterface.extend({
        init: function (pos, payment_method) {
            this._super(...arguments);
            this._pabilo_cancelled = false;
        },

        _sleep_cancelable: function (ms) {
            // Espera en tramos cortos para que "Cancelar" responda de inmediato.
            const step = 250;
            const chunks = Math.ceil(ms / step);
            let promise = Promise.resolve();
            for (let i = 0; i < chunks; i++) {
                promise = promise.then(() => {
                    if (this._pabilo_cancelled) {
                        return Promise.resolve();
                    }
                    return new Promise((resolve) => setTimeout(resolve, step));
                });
            }
            return promise;
        },

        _verify: function (reference, amount) {
            return rpc.query(
                {
                    model: 'pos.payment.method',
                    method: 'pabilo_verify_payment',
                    args: [[this.payment_method.id], reference, amount],
                },
                { shadow: true, timeout: 25000 }
            );
        },

        /**
         * Se llama cuando el cajero pulsa "Enviar" en una línea de pago Pabilo.
         * Pide los últimos 6 dígitos de la referencia (el backend matchea por
         * sufijo) y verifica con reintentos automáticos.
         */
        send_payment_request: async function (cid) {
            this._super(...arguments);
            const line = this.pos.get_order().selected_paymentline;
            const amount = line.amount;
            this._pabilo_cancelled = false;

            const accountHint = this.payment_method.pabilo_account_hint
                ? _.str.sprintf(_t('Cuenta destino: %s. '), this.payment_method.pabilo_account_hint)
                : '';

            const { confirmed, payload: reference } = await Gui.showPopup('NumberPopup', {
                title: _t('Referencia del pago móvil'),
                body: _.str.sprintf(
                    _t('%sMonto: %s. Ingrese los últimos 6 dígitos de la referencia.'),
                    accountHint,
                    line.get_amount_str()
                ),
            });

            if (this._pabilo_cancelled || !confirmed || !reference) {
                return false;
            }

            line.set_payment_status('waitingCard');

            let lastResult = null;
            for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
                if (this._pabilo_cancelled) {
                    return false;
                }

                try {
                    lastResult = await this._verify(reference, amount);
                } catch (error) {
                    await Gui.showPopup('ErrorPopup', {
                        title: _t('Error de Conexión'),
                        body: _t('No se pudo conectar con Pabilo. Verifique su conexión a internet.'),
                    });
                    return false;
                }

                if (lastResult.verified) {
                    if (!lastResult.is_new) {
                        // El movimiento ya fue consumido por una venta anterior:
                        // aceptarlo de nuevo cobraría dos veces el mismo pago.
                        await Gui.showPopup('ErrorPopup', {
                            title: _t('Pago ya registrado'),
                            body: _t('Esta referencia ya fue verificada antes. Pida al cliente un pago nuevo.'),
                        });
                        return false;
                    }
                    line.pabilo_reference = reference;
                    line.pabilo_payment_id = lastResult.payment_id;
                    line.pabilo_is_new = lastResult.is_new;
                    line.set_payment_status('done');
                    line.set_receipt_info(
                        _.str.sprintf(_t('Pabilo ref: %s\n'), reference)
                    );
                    return true;
                }

                if (lastResult.error_code !== RETRYABLE_ERROR) {
                    await Gui.showPopup('ErrorPopup', {
                        title: _t('Error de Verificación Pabilo'),
                        body: lastResult.message || _t('El pago no pudo ser verificado.'),
                    });
                    return false;
                }

                // PAYMENT_NOT_FOUND: el pago puede tardar unos segundos; reintentar.
                if (attempt < MAX_ATTEMPTS) {
                    console.info(`pabilo: pago no encontrado, reintento ${attempt}/${MAX_ATTEMPTS}`);
                    await this._sleep_cancelable(RETRY_DELAY_MS);
                }
            }

            await Gui.showPopup('ErrorPopup', {
                title: _t('Pago no encontrado'),
                body: (lastResult && lastResult.message) || _t('El pago aún no aparece en el banco.'),
            });
            return false;
        },

        send_payment_cancel: function (order, cid) {
            // deletePaymentLine de v16 hace .then() sobre el retorno: debe ser Promise.
            this._pabilo_cancelled = true;
            this._super(...arguments);
            return Promise.resolve(true);
        },
    });

    return PabiloPayment;
});
