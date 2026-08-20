"""2.4.0 — un método de pago (y un diario) por cuenta de Pabilo.

Deliberadamente conservador: **no crea métodos de pago ni renombra nada**. Crear
métodos es cosa del botón de Ajustes, que es explícito y lo pulsa alguien que
sabe lo que está haciendo; una migración que los cree sola dejaría al cliente con
métodos y diarios que no pidió, aparecidos durante un despliegue.

Lo único que arregla por su cuenta es el diario faltante de los métodos que **ya
están en uso**, porque eso no es una función nueva sino un error silencioso: sin
diario, Odoo trata el método como «pagar después» y el cobro nunca entra en caja.

Todo lo demás lo deja en el log, con la ruta exacta de qué pulsar.
"""
import logging

from odoo import SUPERUSER_ID, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Instalación nueva: no hay nada que migrar.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Method = env['pos.payment.method'].with_context(active_test=False)
    Bank = env['pabilo.user.bank']

    metodos = Method.search([('use_payment_terminal', '=', 'pabilo')])
    _logger.info("Pabilo 2.4.0: %s métodos de pago Pabilo encontrados", len(metodos))

    # 1) Diario faltante en métodos ya en uso.
    arreglados, bloqueados = [], []
    for metodo in metodos.filtered(lambda m: not m.journal_id and m.pabilo_user_bank_id):
        try:
            metodo.journal_id = metodo.pabilo_user_bank_id._pabilo_ensure_journal()
            arreglados.append(metodo.name)
        except UserError as e:
            # Sesión POS abierta: Odoo prohíbe escribir en el método. No se
            # revienta el arranque por esto; se deja dicho para que lo repitan
            # con la caja cerrada.
            bloqueados.append('%s (%s)' % (metodo.name, e))
        except Exception:
            _logger.exception("Pabilo 2.4.0: no se pudo crear el diario de %s", metodo.name)
            bloqueados.append(metodo.name)

    if arreglados:
        _logger.info(
            "Pabilo 2.4.0: diario creado para %s métodos que no tenían "
            "(sin diario, el cobro no entraba en caja): %s",
            len(arreglados), ', '.join(arreglados))
    if bloqueados:
        _logger.warning(
            "Pabilo 2.4.0: %s métodos se quedaron sin diario: %s. "
            "Cierra las sesiones POS abiertas y pulsa "
            "Ajustes → Pabilo → Crear Métodos de Pago.",
            len(bloqueados), '; '.join(bloqueados))

    # 2) Cuentas sin método: solo se informa, no se crean.
    sin_metodo = Bank.search([('is_trashed', '=', False)]).filtered(
        lambda b: not Method.search_count([('pabilo_user_bank_id', '=', b.id)]))
    if sin_metodo:
        _logger.info(
            "Pabilo 2.4.0: %s cuentas de Pabilo no tienen método de pago (%s). "
            "Para que cada cobro se asiente en la cuenta que lo recibió, entra a "
            "Ajustes → Pabilo → Crear Métodos de Pago. Nada cambia hasta que lo pulses.",
            len(sin_metodo), ', '.join(sin_metodo.mapped('display_name')))
    else:
        _logger.info("Pabilo 2.4.0: todas las cuentas ya tienen método de pago")

    # 3) Métodos que verifican contra varias cuentas ya no existen como tal: el
    #    POS deja de preguntar cuando el método trae cuenta. Si alguno quedó sin
    #    cuenta, sigue preguntando, y conviene saberlo.
    sin_cuenta = metodos.filtered(lambda m: not m.pabilo_user_bank_id)
    if sin_cuenta:
        _logger.warning(
            "Pabilo 2.4.0: %s métodos no tienen cuenta asignada (%s). Seguirán "
            "preguntando al cajero a qué cuenta llegó el pago, que es justo lo "
            "que descuadra la contabilidad. Asígnales una cuenta.",
            len(sin_cuenta), ', '.join(sin_cuenta.mapped('name')))
