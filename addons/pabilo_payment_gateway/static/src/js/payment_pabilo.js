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

    _verify(reference, amount, bankId, fechaPago) {
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
                ],
                {},
                { timeout: VERIFY_TIMEOUT_MS }
            );
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

        const { confirmed, payload: reference } = await this.popup.add(NumberPopup, {
            title: _t("Referencia del pago"),
            body: _t(
                "%sMonto: %s. Ingrese los últimos 6 dígitos de la referencia.",
                accountHint,
                line.get_amount_str()
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
            result = await this._verify(reference, amount, bankId, fechaPago);
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
                    "Pabilo ref: %s\nCuenta: %s\nFecha: %s\n",
                    reference,
                    bankLabel || "-",
                    fechaPago
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
