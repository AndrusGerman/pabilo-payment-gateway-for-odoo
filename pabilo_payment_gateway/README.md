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
| `models/pos_payment_method.py` | Métodos RPC que llama el POS: `pabilo_verify_payment`, `pabilo_amount_preview` y la resolución del monto en la moneda del banco. |
| `static/src/js/payment_pabilo.js` | `PaymentInterface` del POS: teclado numérico, fecha del pago y selección de cuenta. |
| `static/src/js/models.js` | `register_payment_method('pabilo', …)` y serialización de los campos en la línea de pago. |
| `models/pabilo_verification.py` | Bitácora de lo que este Odoo verificó, para que una venta suspendida se pueda retomar. |
| `models/pabilo_user_bank.py` | Espejo **de solo lectura** de las cuentas de Pabilo, y la creación de un método de pago y un diario por cada una. |

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

## Referencias y ventas suspendidas

Pabilo marca cada movimiento bancario como consumido en cuanto se verifica. Eso es
lo que evita que dos ventas cobren el mismo pago — y también lo que rompía un caso
muy normal en una caja: el cajero valida la referencia, algo interrumpe la venta y
al retomarla la referencia ya no sirve, porque Pabilo responde que el movimiento
fue consumido. Y es verdad: lo consumimos nosotros un minuto antes.

Sin memoria propia no hay forma de distinguir «esto ya lo cobró otra venta» de
«esto lo verifiqué yo y la venta no se cerró». Por eso el módulo lleva una
bitácora, `pabilo.verification`, que se consulta **antes** de llamar a la API.

### Cómo se reconoce una verificación propia

Por dos caminos, y hacen falta los dos:

1. **Referencia, cuenta y monto.** La comparación de la referencia es **por
   sufijo**, no exacta, porque Pabilo matchea así: el cajero teclea los últimos
   dígitos y la misma referencia puede llegar como `704777` una vez y como `4777`
   la siguiente. Resuelve sin tocar la API.
2. **El id del movimiento.** Si el sufijo no alcanza, se llama a Pabilo; cuando
   responde `is_new: false` se busca su `user_bank_payment.id` en la bitácora.
   Ese id es la clave fuerte: Pabilo devuelve el mismo tanto si el pago es nuevo
   como si ya se usó, así que reconoce el caso sin depender de lo que teclee el
   cajero.

### Qué impide cobrar dos veces

Reutilizar exige **todo** a la vez: misma cuenta, referencia compatible, mismo
monto, menos de 24 h y **ninguna venta cerrada**. Al crear el `pos.payment` la
verificación pasa a `consumed` con su venta, y desde ahí no se reutiliza nunca
más: el próximo intento se rechaza diciendo **en qué venta se cobró**.

Un movimiento que consumió otro sistema —otra instalación, la app de Pabilo— no
está en la bitácora, y se sigue rechazando igual que antes.

### Dónde se consulta

**Pabilo → Verificaciones Pabilo.** Es donde se responde «¿por qué me dice que esta
referencia ya se usó?»: si sale como *Cobrada en una venta*, la columna Venta dice
en cuál. Es de solo lectura; la escribe el módulo.

## Un método de pago por cuenta

Pabilo verifica contra una cuenta bancaria concreta, y en Odoo cada cobro se
asienta en el **diario** del método de pago. Si un mismo método sirve para varias
cuentas, esas dos cosas se separan: el cajero dice que el dinero llegó al BDV y
el asiento va al diario de Binance. Se verifica bien y se contabiliza mal.

Por eso el modelo es **una cuenta, un método, un diario**.

### Cómo se crean

**Ajustes → Pabilo → Crear Métodos de Pago.** Sincroniza las cuentas y crea un
`pos.payment.method` por cada una, llamado `Pabilo - <cuenta>`, con un diario de
banco propio.

Es un botón y no algo automático a propósito: crear diarios es tocar
contabilidad, y eso no debe pasar en el cron de madrugada ni a media venta.

Después hay que **añadir los métodos a cada TPV** que los vaya a usar
(Punto de Venta → Configuración → Punto de Venta). El botón no los agrega solo:
cambiar la pantalla de cobro sin que nadie lo pida es peor que un paso de más.

### Qué respeta

- **Los nombres.** El botón solo crea lo que falta; si la cuenta ya tiene método,
  no lo toca. Rename libre desde Odoo.
- **Lo que ya funciona.** No reasigna diarios ya puestos. Sí rellena el diario
  cuando está **vacío**, porque sin él Odoo trata el método como «pagar
  después» y el cobro no entra en caja.
- **El histórico.** Una cuenta eliminada en Pabilo archiva su método, no lo
  borra: hay `pos.payment` viejos apuntando ahí.

Activar y desactivar es el archivado nativo de Odoo, y no tiene ninguna relación
con la API: archivar un método aquí no toca la cuenta en Pabilo.

### Por qué los diarios no llevan moneda

Se crean **sin moneda**, o sea en la de la compañía, aunque el banco registre en
bolívares. `pos.config._check_payment_method_ids` rechaza un método cuyo diario
tenga una moneda distinta a la del TPV, así que un diario en bolívares no se
podría ni agregar a una caja que cobra en dólares. Y es lo correcto:
`pos.payment.amount` está en moneda del TPV; los bolívares solo se usan para
buscar el movimiento en el banco (ver **Multi-moneda**).

### En el POS

Con la cuenta puesta en el método, **el POS ya no pregunta** a cuál llegó el
pago: elegir el método fue elegir la cuenta. El selector queda solo como
respaldo para un método sin cuenta configurada.

## Multi-moneda

Pabilo compara el monto contra el movimiento del banco, que está en la moneda de
la cuenta: **bolívares** en los bancos venezolanos, dólares en Binance Pay. Si el
POS cobra en otra moneda, el monto de la línea no sirve tal cual —pedir un
movimiento de `0,60` cuando al banco entraron `36,00` da siempre
`PAYMENT_AMOUNT_NOT_VALID`— así que se convierte antes de consultar.

La conversión se hace **en el servidor**: el navegador no tiene las tasas de Odoo.

### De dónde sale la tasa

**De las tasas nativas de Odoo** (`res.currency.rate`: Contabilidad →
Configuración → Monedas → Tasas), las mismas que usa el resto del sistema para
facturar y contabilizar. No hay nada que configurar.

`_get_rates` toma la última fila con fecha ≤ hoy, así que la tasa vigente puede
ser de hace días. Cuando no es de hoy, el POS lo dice: «771,07 VES por cada USD,
del 14/08/2026».

Sin tasa, **no se convierte a ciegas**: se responde `NO_CURRENCY_RATE`. Hace falta
porque `_convert` no protesta cuando le falta —`_get_rates` cae en
`COALESCE(..., 1.0)` y devuelve el monto intacto—, así que el fallo sería
invisible y se mandarían dólares creyendo que son bolívares.

Si en el mostrador se cobró con otra tasa, el cajero la corrige en el momento (ver
abajo). Y quien tenga una fuente de tasas de verdad distinta puede heredar
`_pabilo_conversion_rate`.

> **Sobre los módulos de moneda alterna.** Las localizaciones venezolanas que
> pintan un "Restante alterno" en el POS suelen apoyarse en las tasas nativas, así
> que no hace falta hacer nada. Comprobado con `binaural_rate` (Binauraldev), que
> hereda `res.currency` y `res.currency.rate`: su `pos.config.foreign_rate` no está
> guardado, se calcula al vuelo desde la tasa nativa. Es el mismo número.

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
