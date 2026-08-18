# Pabilo Payment Gateway for Odoo

Verificación automática de pagos venezolanos dentro de Odoo 17.

El cajero teclea los **últimos 6 dígitos** de la referencia, confirma la fecha
—ya rellenada con hoy— y Odoo verifica el cobro contra el banco a través de la
API de [Pabilo](https://pabilo.app). Sirve igual para pago móvil, transferencias
y Binance.

## Instalación

```bash
# El addon debe quedar dentro del addons_path de Odoo
cp -r pabilo_payment_gateway /mnt/extra-addons/
odoo -u pabilo_payment_gateway -d <db> --stop-after-init
```

## Configuración

1. **Ajustes → Pabilo**: pega el API Key (appKey) y pulsa **Sincronizar Cuentas**.
2. **Punto de Venta → Métodos de Pago → Agregar Método Pabilo**: elige la cuenta
   bancaria y guarda.
3. Añade el método de pago al punto de venta.

La URL base del backend se guarda en el parámetro de sistema `pabilo.api_url`
(por defecto `https://api.pabilo.app`). Solo hace falta cambiarla si Pabilo
indica otro servidor.

## Arquitectura

| Pieza | Rol |
| :--- | :--- |
| `models/pabilo_client.py` | Cliente HTTP único. Concentra URL base, header `appKey`, parseo y normalización de errores. |
| `models/pos_payment_method.py` | Métodos RPC que llama el POS: `pabilo_verify_payment`, `pabilo_amount_preview` y la resolución del monto (tasa de Odoo, tasa alterna o monto escrito por el cajero). |
| `static/src/js/payment_pabilo.js` | `PaymentInterface` del POS: teclado numérico, fecha del pago y selección de cuenta. |
| `static/src/js/models.js` | `register_payment_method('pabilo', …)` y serialización de los campos en la línea de pago. |
| `models/pabilo_user_bank.py` | Espejo **de solo lectura** de las cuentas de Pabilo. |

El navegador nunca ve el appKey: el JS llama a Odoo por RPC y es el servidor
quien habla con Pabilo.

### Cuentas bancarias de solo lectura

`pabilo.user.bank` es un espejo local. La fuente de verdad es Pabilo, así que el
modelo no acepta escrituras fuera de la sincronización: la ACL no concede
`write`/`create`/`unlink` a ningún grupo, y `create`/`write`/`unlink` exigen el
contexto `pabilo_sync`. Editar en Odoo solo produciría divergencia silenciosa —
la siguiente sincronización sobrescribiría el cambio mientras el POS verifica
contra una cuenta distinta de la que muestra la pantalla.

Las cuentas que Pabilo deja de devolver se marcan `is_trashed` en la siguiente
sincronización, ya que nadie puede borrarlas a mano.

## Contrato con el backend

| Uso | Llamada |
| :--- | :--- |
| Verificar un pago | `POST /userbankpayment/{user_bank_id}/betaserio` → `{bank_reference, amount, movement_type, fecha_pago, source_name}` |
| Secreto del webhook | `GET /me/webhook-secret` |
| Listar cuentas | `GET /me/usersbank` |
| Crear enlace de pago | `POST /v1/paymentlink` |
| Consultar enlace | `GET /paymentlink/{id}/info` |

Autenticación por header `appKey`.

Se considera verificado si y solo si `HTTP 200` **y**
`data.user_bank_payment.status == "paid"`. Los errores llegan como
`{"message": …, "error": "CODE"}`. **Ningún error se reintenta**: el backend ya
consultó el banco antes de responder, así que la respuesta es firme. Lo que puede
tardar es esa consulta, y para eso hay una sola llamada con 110 s de timeout en
el servidor y 115 s en el navegador.

## Traducciones

Los mensajes de runtime están envueltos en `_()`, y el mapa de errores de
`pabilo_client.py` en `_lt()` porque se evalúa al importar el módulo, antes de
que exista usuario o idioma. Para regenerar la plantilla:

```bash
odoo -d <db> --i18n-export=i18n/pabilo_payment_gateway.pot \
     --modules=pabilo_payment_gateway --stop-after-init
```

## Webhook de enlaces de pago

`POST /pabilo/webhook` recibe los cambios de estado. Verifica la firma HMAC-SHA256
que manda Pabilo sobre `"<timestamp>.<cuerpo crudo>"`, compara con `consteq` y
rechaza timestamps de más de 5 minutos. **Sin secreto configurado rechaza todo**,
en vez de aceptar a ciegas.

El secreto lo trae la sincronización desde `GET /me/webhook-secret` y es propio de
cada cuenta de Pabilo. No hay que escribirlo.

Requisito de despliegue: el `dbfilter` de Odoo debe resolver a **una sola base**.
Si coincide con varias, Odoo no sabe cuál usar en una ruta pública y responde 404
en lugar de procesar el webhook.

## Multi-moneda

Pabilo compara el monto contra el movimiento del banco, que está en la moneda de
la cuenta: **bolívares** en los bancos venezolanos, dólares en Binance Pay. Si el
POS cobra en otra moneda, el monto de la línea no sirve tal cual —pedir un
movimiento de `0,60` cuando al banco entraron `36,00` da siempre
`PAYMENT_AMOUNT_NOT_VALID`— así que se convierte antes de consultar.

La conversión se hace **en el servidor**: el navegador no tiene las tasas de Odoo.

### De dónde sale la tasa

Por orden de prioridad:

1. **El monto que escribió el cajero**, si eligió escribirlo.
2. **`pabilo_alt_rate_field`**, si el método de pago dice dónde leer la tasa en el
   POS. Es una ruta con puntos que se busca, en orden, en la línea de pago, en el
   pedido y en el objeto `pos`: por ejemplo `config.foreign_rate`.
3. **Las tasas nativas de Odoo** (`res.currency.rate`: Contabilidad →
   Configuración → Monedas → Tasas), que es el caso normal.

Sin tasa, **no se convierte a ciegas**: se responde `NO_CURRENCY_RATE`. Hace falta
porque `_convert` no protesta cuando le falta —`_get_rates` cae en
`COALESCE(..., 1.0)` y devuelve el monto intacto—, así que el fallo sería invisible
y se mandarían dólares creyendo que son bolívares.

Quien prefiera resolverlo en Python tiene `_pabilo_conversion_rate` como punto de
extensión.

### Cuándo hace falta `pabilo_alt_rate_field`

**Casi nunca.** Los módulos de moneda alterna venezolanos suelen apoyarse en las
tasas nativas de Odoo, y entonces el comportamiento por defecto ya da la cifra que
el cajero ve en pantalla. Comprobado con `binaural_rate` (Binauraldev), que hereda
`res.currency` y `res.currency.rate`: su `pos.config.foreign_rate` es un espejo de
la tasa nativa.

Configúralo solo si el módulo lleva una tasa **propia**, separada de la de Odoo.
Para averiguarlo, compara `res.currency.rate` de la moneda del banco con lo que
pinta el POS como importe alterno: si coinciden, deja el campo vacío.

### Lo que ve el cajero

Cuando hubo conversión, antes de pedir la referencia el POS muestra cuánto se va a
buscar en el banco y con qué tasa salió —con su fecha, si no es de hoy— y deja
elegir: aceptarlo, usar otra tasa o escribir el monto del comprobante del cliente.
Si la moneda del POS ya es la del banco no se pregunta: no hay nada que elegir.

Que el monto lo proponga el navegador no abre ningún hueco: el cajero ya decide el
monto de la línea, y quien valida de verdad es Pabilo contra el movimiento real del
banco.

Se puede apagar el paso con **Elegir Como se Valida en el POS** en el método de
pago.

## Limitaciones conocidas

- Los enlaces de pago quedan en `pending` hasta que se pulsa **Consultar Estado**;
  no hay cron de reconciliación.
- El `metadata` que se envía al crear un enlace de pago no vuelve en el webhook:
  el backend lo toma del pagador, no del enlace.
- `source_name` (caja y cajero) se envía pero el backend todavía no tiene el campo,
  así que no queda guardado.
- `USDT` aparece en la documentación pero no está en el mapa de monedas; se
  enviaría como `VEF`.

## Licencia

OPL-1 (Odoo Proprietary License v1.0)
