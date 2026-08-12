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

        _verify: function (reference, amount, bankId) {
            return rpc.query(
                {
                    model: 'pos.payment.method',
                    method: 'pabilo_verify_payment',
                    args: [[this.payment_method.id], reference, amount, bankId || null],
                },
                { shadow: true, timeout: 25000 }
            );
        },

        _fetch_accounts: async function () {
            // Cuentas disponibles para que el cajero elija a cuál llegó el pago.
            // Se cachea solo en éxito; si falla la red se sigue con la cuenta por defecto.
            if (this._pabilo_accounts) {
                return this._pabilo_accounts;
            }
            try {
                const accounts = await rpc.query(
                    {
                        model: 'pos.payment.method',
                        method: 'pabilo_get_user_banks',
                        args: [[this.payment_method.id]],
                    },
                    { shadow: true, timeout: 10000 }
                );
                this._pabilo_accounts = accounts || [];
            } catch (error) {
                return [];
            }
            return this._pabilo_accounts;
        },

        /**
         * Se llama cuando el cajero pulsa "Enviar" en una línea de pago Pabilo.
         * 1) Si hay varias cuentas, el cajero elige a cuál llegó el pago.
         * 2) Pide los últimos 6 dígitos de la referencia (el backend matchea por sufijo).
         * 3) Verifica con reintentos automáticos contra la cuenta elegida.
         */
        send_payment_request: async function (cid) {
            this._super(...arguments);
            const line = this.pos.get_order().selected_paymentline;
            const amount = line.amount;
            this._pabilo_cancelled = false;

            let bankId = this.payment_method.pabilo_user_bank_id
                ? this.payment_method.pabilo_user_bank_id[0]
                : null;
            let bankLabel = this.payment_method.pabilo_account_hint || '';

            const accounts = await this._fetch_accounts();
            if (this._pabilo_cancelled) {
                return false;
            }
            if (accounts.length > 1) {
                const { confirmed, payload: account } = await Gui.showPopup('SelectionPopup', {
                    title: _t('Cuenta bancaria destino'),
                    body: _t('¿A cuál de estas cuentas envió el cliente el pago?'),
                    list: accounts.map((acc) => ({
                        id: acc.id,
                        label: acc.display_name,
                        isSelected: acc.id === bankId,
                        item: acc,
                    })),
                });
                if (this._pabilo_cancelled || !confirmed || !account) {
                    return false;
                }
                bankId = account.id;
                bankLabel = account.display_name;
            } else if (accounts.length === 1) {
                bankId = accounts[0].id;
                bankLabel = accounts[0].display_name;
            }

            const accountHint = bankLabel
                ? _.str.sprintf(_t('Cuenta destino: %s. '), bankLabel)
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
                    lastResult = await this._verify(reference, amount, bankId);
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
                        _.str.sprintf(_t('Pabilo ref: %s\nCuenta: %s\n'), reference, bankLabel || '-')
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
