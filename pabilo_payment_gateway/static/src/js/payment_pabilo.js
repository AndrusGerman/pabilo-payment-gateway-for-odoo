odoo.define('pabilo_payment_gateway.payment', function (require) {
    'use strict';

    const { Gui } = require('point_of_sale.Gui');
    const { _t } = require('web.core');
    const rpc = require('web.rpc');
    const PaymentInterface = require('point_of_sale.PaymentInterface');

    // No se reintenta ningún error. "Pago no encontrado" parecía el caso a
    // repetir, pero el backend ya consultó el banco antes de responderlo: es un
    // dato firme, igual que un monto que no coincide o una referencia ya usada.
    // Preguntar de nuevo da lo mismo y solo deja al cajero esperando.
    //
    // Lo que sí puede tardar es la consulta al banco, y para eso está el timeout
    // largo de una sola llamada.
    const VERIFY_TIMEOUT_MS = 115000;

    const PabiloPayment = PaymentInterface.extend({
        init: function (pos, payment_method) {
            this._super(...arguments);
            this._pabilo_cancelled = false;
        },

        // Fecha de hoy en la zona del navegador, en YYYY-MM-DD. toISOString()
        // daria UTC, que de noche cae en el dia siguiente y buscaria mal.
        _today: function () {
            const d = new Date();
            const mes = String(d.getMonth() + 1).padStart(2, '0');
            const dia = String(d.getDate()).padStart(2, '0');
            return `${d.getFullYear()}-${mes}-${dia}`;
        },

        // Identifica la caja y el cajero que cobran, para poder rastrear el pago
        // desde Pabilo cuando varias cajas comparten la misma cuenta bancaria.
        _source_name: function () {
            const config = this.pos.config;
            const cashier = this.pos.get_cashier && this.pos.get_cashier();
            const parts = [config && config.name, cashier && cashier.name].filter(Boolean);
            return parts.join(' - ').slice(0, 120);
        },

        // Moneda de la linea de pago. El servidor la necesita para pasar el
        // monto a la del banco, que es contra la que Pabilo compara.
        _pos_currency_id: function () {
            return (this.pos.currency && this.pos.currency.id) || null;
        },

        // Lo que teclea el cajero viene con el separador decimal de la base.
        _parse_number: function (payload) {
            const numero = parseFloat(String(payload || '').replace(',', '.'));
            return isNaN(numero) ? null : numero;
        },

        // Identificador que el POS le da al pedido en el navegador, antes de que
        // exista en Odoo. Ata la verificación a esta venta, para reconocerla si se
        // suspende y se retoma.
        _order_uid: function () {
            const order = this.pos.get_order();
            return (order && order.uid) || null;
        },

        /**
         * Pone en la línea de pago el equivalente, en moneda del POS, del monto
         * que se va a verificar en el banco.
         *
         * El servidor manda `pos_amount` ya convertido y redondeado con la
         * precisión de la moneda del POS, y lo deja en 0 cuando no hay nada que
         * traducir (misma moneda, o sin tasa con la que dividir).
         */
        _apply_line_amount: function (line, preview) {
            if (!preview || !preview.converted || !preview.pos_amount) {
                return false;
            }
            const nuevo = preview.pos_amount;
            if (Math.abs((line.amount || 0) - nuevo) < 0.005) {
                return false;
            }
            const anterior = line.amount;
            line.set_amount(nuevo);
            console.info(
                '[pabilo] línea de pago ajustada de %s a %s: es el equivalente de ' +
                    'los %s que se van a verificar en el banco.',
                anterior,
                nuevo,
                preview.label
            );
            return true;
        },

        _verify: function (reference, amount, bankId, fechaPago, rate, amountInBank) {
            // El timeout del RPC debe superar el del servidor (110 s) para que
            // gane el mensaje real de Pabilo y no un error de red genérico.
            return rpc.query(
                {
                    model: 'pos.payment.method',
                    method: 'pabilo_verify_payment',
                    args: [
                        [this.payment_method.id],
                        reference,
                        amount,
                        bankId || null,
                        this._source_name(),
                        fechaPago || null,
                        this._pos_currency_id(),
                        rate || null,
                        amountInBank === null || amountInBank === undefined ? null : amountInBank,
                        this._order_uid(),
                    ],
                },
                { shadow: true, timeout: VERIFY_TIMEOUT_MS }
            );
        },

        // Monto que de verdad se va a buscar en el banco, ya convertido. Solo
        // informa: el que se verifica lo recalcula el servidor. Si la red falla
        // se devuelve null y el cajero ve el monto de la linea, que es peor
        // texto pero no cambia lo que se cobra.
        _amount_preview: async function (amount, bankId, rate, amountInBank) {
            try {
                return await rpc.query(
                    {
                        model: 'pos.payment.method',
                        method: 'pabilo_amount_preview',
                        args: [
                            [this.payment_method.id],
                            amount,
                            bankId || null,
                            this._pos_currency_id(),
                            rate || null,
                            amountInBank === null || amountInBank === undefined ? null : amountInBank,
                        ],
                    },
                    { shadow: true, timeout: 10000 }
                );
            } catch (error) {
                return null;
            }
        },

        /**
         * Deja elegir contra que monto se valida, cuando hubo que convertir.
         *
         * La tasa es la de Odoo (Contabilidad → Configuración → Monedas →
         * Tasas), la misma que usa el resto del sistema. Pero la tasa contable no
         * siempre es la del mostrador, asi que se puede cambiar la tasa o
         * escribir directamente el monto del comprobante del cliente.
         *
         * Devuelve {preview, rate, amountInBank} o null si el cajero cancelo.
         */
        _choose_amount: async function (line, preview, bankId, rate) {
            const { confirmed, payload: opcion } = await Gui.showPopup('SelectionPopup', {
                title: _t('¿Cuánto llegó al banco?'),
                body: _.str.sprintf(
                    _t('La línea de pago es %s.'),
                    preview.line_label || line.get_amount_str()
                ),
                list: [
                    {
                        id: 1,
                        label: _.str.sprintf(
                            _t('%s  ·  tasa de Odoo: %s'),
                            preview.label,
                            preview.rate_label
                        ),
                        isSelected: true,
                        item: 'aceptar',
                    },
                    { id: 2, label: _t('Usar otra tasa…'), item: 'tasa' },
                    {
                        id: 3,
                        label: _.str.sprintf(
                            _t('Escribir el monto en %s…'),
                            preview.currency_name
                        ),
                        item: 'monto',
                    },
                ],
            });
            if (this._pabilo_cancelled || !confirmed || !opcion) {
                return null;
            }
            if (opcion === 'aceptar') {
                return { preview: preview, rate: rate, amountInBank: null };
            }

            const esTasa = opcion === 'tasa';
            const res = await Gui.showPopup('NumberPopup', {
                title: esTasa ? _t('Tasa de cambio') : _t('Monto que llegó al banco'),
                body: esTasa
                    ? _.str.sprintf(
                          _t('¿Cuántos %s por cada %s?'),
                          preview.currency_name,
                          preview.pos_currency_name
                      )
                    : _.str.sprintf(_t('Monto exacto en %s.'), preview.currency_name),
                startingValue: esTasa ? preview.rate : preview.amount,
                isInputSelected: true,
            });
            if (this._pabilo_cancelled || !res.confirmed) {
                return null;
            }
            const valor = this._parse_number(res.payload);
            if (!valor || valor <= 0) {
                await Gui.showPopup('ErrorPopup', {
                    title: _t('Valor no válido'),
                    body: _t('Debe ser un número mayor que cero.'),
                });
                return null;
            }

            // Se recalcula en el servidor: asi el numero y su formato salen del
            // mismo sitio que luego verifica, y no pueden discrepar.
            const nuevaTasa = esTasa ? valor : null;
            const nuevoMonto = esTasa ? null : valor;
            const recalculo = await this._amount_preview(
                line.amount,
                bankId,
                nuevaTasa,
                nuevoMonto
            );
            if (this._pabilo_cancelled) {
                return null;
            }
            if (!recalculo || recalculo.error_code) {
                await Gui.showPopup('ErrorPopup', {
                    title: _t('No se pudo calcular el monto'),
                    body: (recalculo && recalculo.message) || _t('Intente de nuevo.'),
                });
                return null;
            }
            return {
                preview: recalculo,
                rate: nuevaTasa,
                amountInBank: esTasa ? null : recalculo.amount,
            };
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
         * 3) Pide la fecha del pago, ya rellenada con hoy.
         * 4) Verifica una sola vez: la respuesta del backend es firme.
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

            // Con la cuenta puesta en el método no se pregunta nada: elegir el
            // método ya fue elegir la cuenta, y su diario contable es el de esa
            // cuenta. Preguntar aquí es lo que permitía verificar contra un
            // banco y asentar el cobro en otro.
            //
            // El selector queda solo para un método sin cuenta configurada:
            // instalaciones que aún no pulsaron «Crear Métodos de Pago».
            if (!bankId) {
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
            }

            const accountHint = bankLabel
                ? _.str.sprintf(_t('Cuenta destino: %s. '), bankLabel)
                : '';

            // Con multi-moneda la línea dice 0,60 $ pero al banco entraron
            // 36,00 Bs, que es lo que Pabilo va a buscar. Se muestra ese, que es
            // el que el cajero puede contrastar con el comprobante del cliente.
            // La tasa la resuelve el servidor con las tasas nativas de Odoo.
            // Estas dos solo se llenan si el cajero decide otra cosa.
            let rate = null;
            let amountInBank = null;

            let preview = await this._amount_preview(amount, bankId, rate, null);
            if (this._pabilo_cancelled) {
                return false;
            }
            if (preview && preview.error_code) {
                // Problema de configuración: la verificación daría el mismo
                // error, así que no se hace teclear la referencia para nada.
                await Gui.showPopup('ErrorPopup', {
                    title: _t('No se puede verificar en esta cuenta'),
                    body: preview.message || _t('Revise la configuración de monedas en Odoo.'),
                });
                return false;
            }

            // Solo se pregunta cuando hubo conversión. Si el POS ya cobra en la
            // moneda del banco no hay nada que elegir y no se gasta un toque.
            if (preview && preview.needs_confirm) {
                const eleccion = await this._choose_amount(line, preview, bankId, rate);
                if (!eleccion) {
                    return false;
                }
                preview = eleccion.preview;
                rate = eleccion.rate;
                amountInBank = eleccion.amountInBank;
            }

            // El monto que el cajero confirmó manda sobre la línea de pago.
            //
            // Sin esto el cobro parcial es imposible cuando el banco cobra en otra
            // moneda. El teclado del POS recorta lo que se escribe al saldo
            // pendiente **en la moneda del POS** (Odoo pone
            // `maxValue = get_due()` cuando la caja no tiene método de efectivo,
            // y NumberBuffer recorta en silencio), mientras que el cajero teclea
            // bolívares. Cualquier cifra en bolívares supera ese tope, se recorta
            // al total exacto, y la venta se cierra como pagada completa aunque el
            // cliente haya abonado una parte.
            //
            // Diciéndole nosotros el monto, el teclado deja de estorbar: el cajero
            // dice cuánto llegó al banco y la línea queda en su equivalente.
            //
            // Solo cuando hubo conversión: si la moneda del POS ya es la del banco
            // no hay nada que traducir y no se toca nada.
            this._apply_line_amount(line, preview);

            const amountLabel = (preview && preview.label) || line.get_amount_str();

            const { confirmed, payload: reference } = await Gui.showPopup('NumberPopup', {
                title: _t('Referencia del pago'),
                body: _.str.sprintf(
                    _t('%sMonto: %s. Ingrese los últimos 6 dígitos de la referencia.'),
                    accountHint,
                    amountLabel
                ),
            });

            if (this._pabilo_cancelled || !confirmed || !reference) {
                return false;
            }

            // Fecha del pago, ya rellenada con hoy: el caso normal es confirmar
            // sin escribir nada. Solo se toca para un pago de otro dia.
            const hoy = this._today();
            const fechaRes = await Gui.showPopup('TextInputPopup', {
                title: _t('Fecha del pago'),
                body: _t('Formato AAAA-MM-DD. Dejar como está si el pago es de hoy.'),
                startingValue: hoy,
                placeholder: hoy,
            });
            if (this._pabilo_cancelled || !fechaRes.confirmed) {
                return false;
            }
            const fechaPago = (fechaRes.payload || '').trim() || hoy;

            // 'waitingCard' es lo que pinta el spinner de "buscando" en la
            // línea de pago. La llamada es única y puede tardar hasta 110 s, que
            // es lo que el banco puede demorar en responder.
            line.set_payment_status('waitingCard');

            let result = null;
            try {
                result = await this._verify(
                    reference,
                    amount,
                    bankId,
                    fechaPago,
                    rate,
                    amountInBank
                );
            } catch (error) {
                await Gui.showPopup('ErrorPopup', {
                    title: _t('Sin respuesta de Pabilo'),
                    body: _t('La consulta tardó demasiado o no hubo conexión. Verifique la red e intente de nuevo.'),
                });
                return false;
            }

            if (this._pabilo_cancelled) {
                return false;
            }

            if (result.verified) {
                if (!result.is_new) {
                    // El movimiento ya fue consumido y no fue por una venta
                    // nuestra sin cerrar: aceptarlo cobraría dos veces el mismo
                    // pago. Lo de la venta suspendida ya lo resolvió el servidor
                    // contra su bitácora, antes de llegar aquí.
                    await Gui.showPopup('ErrorPopup', {
                        title: _t('Pago ya registrado'),
                        body:
                            result.message ||
                            _t('Esta referencia ya fue verificada antes. Pida al cliente un pago nuevo.'),
                    });
                    return false;
                }
                if (result.reused) {
                    // Venta que se retomó: se aceptó desde la bitácora, sin
                    // volver a consultar a Pabilo ni gastar otro crédito.
                    console.info(
                        '[pabilo] referencia %s aceptada desde la bitácora local: ' +
                            'ya la habíamos verificado y ninguna venta la cobró.',
                        reference
                    );
                }
                line.pabilo_reference = reference;
                line.pabilo_payment_id = result.payment_id;
                line.pabilo_is_new = result.is_new;
                line.set_payment_status('done');
                line.set_receipt_info(
                    _.str.sprintf(
                        _t('Pabilo ref: %s\nCuenta: %s\nMonto verificado: %s\n'),
                        reference,
                        bankLabel || '-',
                        amountLabel
                    )
                );
                return true;
            }

            // Cualquier fallo es definitivo: se muestra el motivo real y se corta.
            // "Pago no encontrado" se trata igual que el resto porque también es
            // un dato firme: el backend ya revisó el banco.
            const notFound = result.error_code === 'PAYMENT_NOT_FOUND';
            await Gui.showPopup('ErrorPopup', {
                title: notFound ? _t('Pago no encontrado') : _t('Error de Verificación Pabilo'),
                body: result.message || _t('El pago no pudo ser verificado.'),
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
