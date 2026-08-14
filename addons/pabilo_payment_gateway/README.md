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
(por defecto `https://api.pabilo.app`). Para un backend local con Odoo en Docker
usa `http://host.docker.internal:3349`; `127.0.0.1` apuntaría al propio contenedor.

## Arquitectura

| Pieza | Rol |
| :--- | :--- |
| `models/pabilo_client.py` | Cliente HTTP único. Concentra URL base, header `appKey`, parseo y normalización de errores. |
| `models/pos_payment_method.py` | Método RPC `pabilo_verify_payment` que llama el POS. |
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

LGPL-3
