/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/payment/payment_interface";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { NumberPopup } from "@point_of_sale/app/utils/input_popups/number_popup";
import { SelectionPopup } from "@point_of_sale/app/utils/input_popups/selection_popup";
import { TextInputPopup } from "@point_of_sale/app/utils/input_popups/text_input_popup";

// No se reintenta ningun error. "Pago no encontrado" parecia el caso a repetir,
// pero el backend ya consulto el banco antes de responderlo: es un dato firme,
// igual que un monto que no coincide o una referencia ya usada. Preguntar de
// nuevo da lo mismo y solo deja al cajero esperando.
//
// Lo que si puede tardar es la consulta al banco, y para eso esta el timeout
// largo de una sola llamada.
const VERIFY_TIMEOUT_MS = 115000;

export class PaymentPabilo extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this._pabilo_cancelled = false;
        this._pabilo_accounts = null;
    }

    get popup() {
        return this.env.services.popup;
    }

    get orm() {
        return this.env.services.orm;
    }

    // Fecha de hoy en la zona del navegador, en YYYY-MM-DD. toISOString() daria
    // UTC, que de noche cae en el dia siguiente y buscaria mal.
    get today() {
        const d = new Date();
        const mes = String(d.getMonth() + 1).padStart(2, "0");
        const dia = String(d.getDate()).padStart(2, "0");
        return `${d.getFullYear()}-${mes}-${dia}`;
    }

    // Identifica la caja y el cajero que cobran, para poder rastrear el pago
    // desde Pabilo cuando varias cajas comparten la misma cuenta bancaria.
    get sourceName() {
        const config = this.pos.config;
        const cashier = this.pos.get_cashier && this.pos.get_cashier();
        const parts = [config && config.name, cashier && cashier.name].filter(Boolean);
        return parts.join(" - ").slice(0, 120);
    }

    // Moneda de la linea de pago. El servidor la necesita para pasar el monto a
    // la del banco, que es contra la que Pabilo compara.
    get posCurrencyId() {
        return (this.pos.currency && this.pos.currency.id) || null;
    }

    // Lee un valor por una ruta con puntos: 'config.tasa_del_dia'.
    _readPath(root, path) {
        return path.split(".").reduce(
            (obj, key) => (obj === null || obj === undefined ? undefined : obj[key]),
            root
        );
    }

    // Tasa de un modulo de moneda alterna, si el metodo de pago dice donde
    // buscarla. Estos modulos pintan un "restante alterno" con una tasa propia
    // que no tiene por que ser la de Odoo; si existe, esa es la que el cliente
    // vio, asi que es la que se propone.
    _altRate(line) {
        const path = this.payment_method.pabilo_alt_rate_field;
        if (!path) {
            return null;
        }
        const order = this.pos.get_order();
        for (const root of [line, order, this.pos]) {
            const numero = parseFloat(this._readPath(root, path));
            if (!isNaN(numero) && numero > 0) {
                return numero;
            }
        }
        console.warn("[pabilo] no se encontro la tasa alterna en la ruta:", path);
        return null;
    }

    // Lo que teclea el cajero viene con el separador decimal de la base.
    _parseNumber(payload) {
        const numero = parseFloat(String(payload || "").replace(",", "."));
        return isNaN(numero) ? null : numero;
    }

    _verify(reference, amount, bankId, fechaPago, rate, amountInBank) {
        // El timeout del ORM debe superar el del servidor (110 s) para que gane
        // el mensaje real de Pabilo y no un error de red generico.
        return this.orm.silent
            .call(
                "pos.payment.method",
                "pabilo_verify_payment",
                [
                    [this.payment_method.id],
                    reference,
                    amount,
                    bankId || null,
                    this.sourceName,
                    fechaPago || null,
                    this.posCurrencyId,
                    rate || null,
                    amountInBank === null || amountInBank === undefined ? null : amountInBank,
                ],
                {},
                { timeout: VERIFY_TIMEOUT_MS }
            );
    }

    // Monto que de verdad se va a buscar en el banco, ya convertido. Solo
    // informa: el que se verifica lo recalcula el servidor. Si la red falla se
    // devuelve null y el cajero ve el monto de la linea, que es peor texto pero
    // no cambia lo que se cobra.
    async _amount_preview(amount, bankId, rate, amountInBank) {
        try {
            return await this.orm.silent.call(
                "pos.payment.method",
                "pabilo_amount_preview",
                [
                    [this.payment_method.id],
                    amount,
                    bankId || null,
                    this.posCurrencyId,
                    rate || null,
                    amountInBank === null || amountInBank === undefined ? null : amountInBank,
                ],
                {},
                { timeout: 10000 }
            );
        } catch (error) {
            return null;
        }
    }

    /**
     * Deja elegir contra que monto se valida, cuando hubo que convertir.
     *
     * La tasa por defecto es la de Odoo (Contabilidad → Configuración → Monedas
     * → Tasas), la misma que usa el resto del sistema. Pero la tasa contable no
     * siempre es la del mostrador, asi que se puede cambiar la tasa o escribir
     * directamente el monto del comprobante del cliente.
     *
     * Devuelve {preview, rate, amountInBank} o null si el cajero cancelo.
     */
    async _chooseAmount(line, preview, bankId, rate) {
        const origen = preview.source === "alterno" ? _t("tasa del POS") : _t("tasa de Odoo");
        const { confirmed, payload: opcion } = await this.popup.add(SelectionPopup, {
            title: _t("¿Cuánto llegó al banco?"),
            body: _t("La línea de pago es %s.", preview.line_label || line.get_amount_str()),
            list: [
                {
                    id: 1,
                    label: _t("%s  ·  %s: %s", preview.label, origen, preview.rate_label),
                    isSelected: true,
                    item: "aceptar",
                },
                { id: 2, label: _t("Usar otra tasa…"), item: "tasa" },
                {
                    id: 3,
                    label: _t("Escribir el monto en %s…", preview.currency_name),
                    item: "monto",
                },
            ],
        });
        if (this._pabilo_cancelled || !confirmed || !opcion) {
            return null;
        }
        if (opcion === "aceptar") {
            return { preview: preview, rate: rate, amountInBank: null };
        }

        const esTasa = opcion === "tasa";
        const res = await this.popup.add(NumberPopup, {
            title: esTasa ? _t("Tasa de cambio") : _t("Monto que llegó al banco"),
            body: esTasa
                ? _t(
                      "¿Cuántos %s por cada %s?",
                      preview.currency_name,
                      preview.pos_currency_name
                  )
                : _t("Monto exacto en %s.", preview.currency_name),
            startingValue: esTasa ? preview.rate : preview.amount,
            isInputSelected: true,
        });
        if (this._pabilo_cancelled || !res.confirmed) {
            return null;
        }
        const valor = this._parseNumber(res.payload);
        if (!valor || valor <= 0) {
            await this.popup.add(ErrorPopup, {
                title: _t("Valor no válido"),
                body: _t("Debe ser un número mayor que cero."),
            });
            return null;
        }

        // Se recalcula en el servidor: asi el numero y su formato salen del mismo
        // sitio que luego verifica, y no pueden discrepar.
        const nuevaTasa = esTasa ? valor : null;
        const nuevoMonto = esTasa ? null : valor;
        const recalculo = await this._amount_preview(line.amount, bankId, nuevaTasa, nuevoMonto);
        if (this._pabilo_cancelled) {
            return null;
        }
        if (!recalculo || recalculo.error_code) {
            await this.popup.add(ErrorPopup, {
                title: _t("No se pudo calcular el monto"),
                body: (recalculo && recalculo.message) || _t("Intente de nuevo."),
            });
            return null;
        }
        return {
            preview: recalculo,
            rate: nuevaTasa,
            amountInBank: esTasa ? null : recalculo.amount,
        };
    }

    async _fetch_accounts() {
        // Cuentas disponibles para que el cajero elija a cuál llegó el pago.
        // Se cachea solo en éxito; si falla la red se sigue con la cuenta por defecto.
        if (this._pabilo_accounts) {
            return this._pabilo_accounts;
        }
        try {
            const accounts = await this.orm.silent.call(
                "pos.payment.method",
                "pabilo_get_user_banks",
                [[this.payment_method.id]]
            );
            this._pabilo_accounts = accounts || [];
        } catch (error) {
            return [];
        }
        return this._pabilo_accounts;
    }

    /**
     * Se llama cuando el cajero pulsa "Enviar" en una línea de pago Pabilo.
     * 1) Si hay varias cuentas, el cajero elige a cuál llegó el pago.
     * 2) Pide los últimos 6 dígitos de la referencia (el backend matchea por sufijo).
     * 3) Pide la fecha del pago, ya rellenada con hoy.
     * 4) Verifica una sola vez: la respuesta del backend es firme.
     */
    async send_payment_request(cid) {
        await super.send_payment_request(cid);
        const line = this.pos.get_order().selected_paymentline;
        const amount = line.amount;
        this._pabilo_cancelled = false;

        let bankId = this.payment_method.pabilo_user_bank_id
            ? this.payment_method.pabilo_user_bank_id[0]
            : null;
        let bankLabel = this.payment_method.pabilo_account_hint || "";

        const accounts = await this._fetch_accounts();
        if (this._pabilo_cancelled) {
            return false;
        }
        if (accounts.length > 1) {
            const { confirmed, payload: account } = await this.popup.add(SelectionPopup, {
                title: _t("Cuenta bancaria destino"),
                body: _t("¿A cuál de estas cuentas envió el cliente el pago?"),
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

        const accountHint = bankLabel ? _t("Cuenta destino: %s. ", bankLabel) : "";

        // Con multi-moneda la linea dice 0,60 $ pero al banco entraron 36,00 Bs,
        // que es lo que Pabilo va a buscar. Se muestra ese, que es el que el
        // cajero puede contrastar con el comprobante del cliente.
        // Si hay un modulo de moneda alterna con tasa propia, esa es la que el
        // cliente vio en pantalla, asi que se propone antes que la de Odoo.
        let rate = this._altRate(line);
        let amountInBank = null;

        let preview = await this._amount_preview(amount, bankId, rate, null);
        if (this._pabilo_cancelled) {
            return false;
        }
        if (preview && preview.error_code) {
            // Problema de configuracion: la verificacion daria el mismo error,
            // asi que no se hace teclear la referencia para nada.
            await this.popup.add(ErrorPopup, {
                title: _t("No se puede verificar en esta cuenta"),
                body: preview.message || _t("Revise la configuración de monedas en Odoo."),
            });
            return false;
        }

        // Solo se pregunta cuando hubo conversion. Si el POS ya cobra en la
        // moneda del banco no hay nada que elegir y no se gasta un toque.
        if (preview && preview.needs_confirm) {
            const eleccion = await this._chooseAmount(line, preview, bankId, rate);
            if (!eleccion) {
                return false;
            }
            preview = eleccion.preview;
            rate = eleccion.rate;
            amountInBank = eleccion.amountInBank;
        }

        const amountLabel = (preview && preview.label) || line.get_amount_str();

        const { confirmed, payload: reference } = await this.popup.add(NumberPopup, {
            title: _t("Referencia del pago"),
            body: _t(
                "%sMonto: %s. Ingrese los últimos 6 dígitos de la referencia.",
                accountHint,
                amountLabel
            ),
        });

        if (this._pabilo_cancelled || !confirmed || !reference) {
            return false;
        }

        // Fecha del pago, ya rellenada con hoy: el caso normal es confirmar sin
        // escribir nada. Solo se toca para un pago de otro dia.
        const hoy = this.today;
        const fechaRes = await this.popup.add(TextInputPopup, {
            title: _t("Fecha del pago"),
            body: _t("Formato AAAA-MM-DD. Dejar como está si el pago es de hoy."),
            startingValue: hoy,
            placeholder: hoy,
        });
        if (this._pabilo_cancelled || !fechaRes.confirmed) {
            return false;
        }
        const fechaPago = (fechaRes.payload || "").trim() || hoy;

        // "waitingCard" es lo que pinta el spinner de "buscando" en la linea de
        // pago. La llamada es unica y puede tardar hasta 110 s, que es lo que el
        // banco puede demorar en responder.
        line.set_payment_status("waitingCard");

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
            await this.popup.add(ErrorPopup, {
                title: _t("Sin respuesta de Pabilo"),
                body: _t(
                    "La consulta tardó demasiado o no hubo conexión. Verifique la red e intente de nuevo."
                ),
            });
            return false;
        }

        if (this._pabilo_cancelled) {
            return false;
        }

        if (result.verified) {
            if (!result.is_new) {
                // El movimiento ya fue consumido por una venta anterior:
                // aceptarlo de nuevo cobraría dos veces el mismo pago.
                await this.popup.add(ErrorPopup, {
                    title: _t("Pago ya registrado"),
                    body: _t(
                        "Esta referencia ya fue verificada antes. Pida al cliente un pago nuevo."
                    ),
                });
                return false;
            }
            line.pabilo_reference = reference;
            line.pabilo_payment_id = result.payment_id;
            line.pabilo_is_new = result.is_new;
            line.set_payment_status("done");
            line.set_receipt_info(
                _t(
                    "Pabilo ref: %s\nCuenta: %s\nFecha: %s\nMonto verificado: %s\n",
                    reference,
                    bankLabel || "-",
                    fechaPago,
                    amountLabel
                )
            );
            return true;
        }

        // Cualquier fallo es definitivo: se muestra el motivo real y se corta.
        // "Pago no encontrado" se trata igual que el resto porque también es un
        // dato firme: el backend ya revisó el banco.
        const notFound = result.error_code === "PAYMENT_NOT_FOUND";
        await this.popup.add(ErrorPopup, {
            title: notFound ? _t("Pago no encontrado") : _t("Error de Verificación Pabilo"),
            body: result.message || _t("El pago no pudo ser verificado."),
        });
        return false;
    }

    send_payment_cancel(order, cid) {
        this._pabilo_cancelled = true;
        super.send_payment_cancel(order, cid);
        return Promise.resolve(true);
    }
}
