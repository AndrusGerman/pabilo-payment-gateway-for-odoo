# Changelog

Formato: `<serie>.<mayor>.<menor>.<parche>`, como exige el manifest de Odoo. Los
tres últimos números van **iguales en las dos ramas**, así que `2.0.0` significa
lo mismo en `16.0` y en `17.0`.

## 16.0.2.6.0 — el cobro parcial en otra moneda ya es posible

Con el banco cobrando en otra moneda que el POS, **un pago parcial cerraba la
venta como pagada completa**. Reportado desde el POS del cliente: una venta de
0,18 $ (138,80 Bs) quedaba en «Restantes 0,00» tras intentar abonar una parte.

No era un bug de un solo módulo, sino de tres piezas que solas están bien:

1. **Odoo core**: si la caja no tiene método de efectivo, limita el teclado al
   saldo pendiente **en la moneda del POS** (`maxValue = get_due()`).
2. **`NumberBuffer`** recorta en silencio lo tecleado a ese tope
   (`NumberBuffer.js:301`).
3. La **moneda alterna** de la localización muestra el total en bolívares, así
   que el cajero teclea bolívares.

Cualquier cifra en bolívares supera un tope expresado en dólares, se recorta al
total exacto, y la venta se cierra. Por eso con Binance —misma moneda que el
POS— sí funcionaba. Y agregar un método de efectivo lo empeora: sin tope,
teclear 100 pensando en bolívares cobra **100 dólares**.

- **El monto que el cajero confirma ahora manda sobre la línea de pago.** Dice
  cuánto llegó al banco y la línea queda en su equivalente, así que el teclado
  deja de estorbar porque ya no se usa para esto. Con 10,00 Bs de total y 5,00 Bs
  abonados, la línea queda en 1,00 $ y el restante en 1,00 $.
- **`pabilo_amount_preview` devuelve `pos_amount`**: el equivalente ya convertido
  y redondeado con la precisión de la moneda del POS. La división se hace en el
  servidor, no en el navegador.
- **Solo actúa cuando hubo conversión.** Si la moneda del POS ya es la del banco
  —Binance en USD sobre una caja en USD— devuelve `pos_amount = 0` y el POS no
  toca la línea.

Efecto secundario a tener en cuenta: al fijar nosotros el monto se salta el tope
de Odoo, así que si el cajero declara más de lo que vale la venta, el exceso lo
trata Odoo como vuelto.

> **Nota de ramas:** esta versión sale solo en `16.0`. La rama `17.0` se queda en
> `2.5.0` a propósito hasta que se porte, así que por primera vez los tres
> últimos números no coinciden entre series.

## 16.0.2.5.0 / 17.0.2.5.0 — una venta suspendida se retoma con la misma referencia

El cajero validaba una referencia, la venta se suspendía o salía del POS sin
facturar, y al retomarla esa referencia **ya no servía**: Pabilo respondía que el
movimiento fue consumido y el POS lo rechazaba. Y Pabilo tenía razón —lo
consumimos nosotros un minuto antes—, pero sin memoria propia no había forma de
distinguir «esto ya lo cobró otra venta» de «esto lo verifiqué yo y la venta no
se cerró». El cliente se quedaba sin poder cobrar un pago que sí había recibido.

- **Modelo nuevo `pabilo.verification`**: bitácora de cada verificación que hace
  este Odoo, con la referencia, el movimiento de Pabilo, el monto, la cuenta y el
  pedido del POS. Se consulta **antes** de llamar a la API.
- **Retomar una venta ya no falla.** Si la verificación es nuestra y ninguna venta
  la cobró, se acepta. Cuando se resuelve por la bitácora no hace falta ni
  consultar al banco, o sea que tampoco se espera.
- **Se reconoce la referencia aunque el cajero teclee otra cantidad de dígitos.**
  Pabilo matchea por sufijo, así que la misma referencia puede llegar como
  `704777` y luego como `4777`; compararlas con `=` las tomaría por distintas.
- **Y si el sufijo no alcanza, manda el id del movimiento.** Pabilo devuelve el
  mismo `user_bank_payment.id` tanto si el pago es nuevo como si ya se usó, así
  que cuando responde `is_new: false` se busca ese id en la bitácora: si es
  nuestro y está sin cobrar, se acepta.
- **Cuando sí hay que rechazar, el mensaje dice en qué venta se cobró**, en vez
  del «ya fue verificada antes» a secas. Si el pedido aún no tiene número de
  secuencia se usa su referencia del POS: nunca se le dice al cajero «cobrada en
  la venta /».
- **La venta cerrada cierra la puerta.** Al crear el `pos.payment`, la
  verificación pasa a `consumed` con su venta y no se vuelve a reutilizar.
- **Menú «Verificaciones Pabilo»** con filtros por estado. Es la pantalla que
  contesta «¿por qué me dice que esta referencia ya se usó?».

Lo que evita cobrar dos veces: reutilizar exige **misma cuenta, referencia
compatible, mismo monto, menos de 24 h y ninguna venta cerrada**. Un movimiento
que consumió otro sistema no está en la bitácora y se sigue rechazando.

## 16.0.2.4.2 / 17.0.2.4.2 — un método borrado ya no deja la caja sin abrir

- **Guarda al restaurar los pedidos guardados.** El POS conserva los pedidos sin
  pagar en el navegador y los reconstruye al arrancar. `Payment.init_from_JSON`
  de Odoo hace, sin comprobar nada:

  ```js
  this.payment_method = this.pos.payment_methods_by_id[json.payment_method_id];
  this.name = this.payment_method.name;
  ```

  Si ese método ya no está —lo borraron, lo archivaron o lo quitaron de la
  caja— eso es `undefined.name`: pantalla roja y **la sesión no abre**. El
  constructor sí tiene la guarda («Please configure a payment method in your
  POS»); la ruta de restauración no.

  Con un método de pago por cuenta, ese conjunto cambia más que antes, así que
  ahora se descarta la línea huérfana y el pedido se abre **sin ella**, con su
  saldo pendiente. Queda un aviso en la consola con el pedido, el monto y el id
  del método que falta.

  La guarda es genérica y no solo para Pabilo: cuando el método ya no existe, no
  hay forma de saber de quién era.

## 16.0.2.4.1 / 17.0.2.4.1 — los menús dicen que son de Pabilo

- **Nuevo menú «Métodos de Pago Pabilo»** en la app. Antes no había ninguno: para
  ver los métodos había que ir a la acción genérica de Odoo, que lista **todos**
  los del POS —los de Pabilo y los que no— y con su propio nombre en el
  breadcrumb («Settings / Payments Methods»). El menú nuevo filtra por
  `use_payment_terminal = 'pabilo'` y trae una lista con la cuenta y el diario de
  cada uno a la vista.
- **«Cuentas Bancarias» → «Cuentas Bancarias Pabilo»** y **«Agregar Método de
  Pago» → «Agregar Método de Pago Pabilo».** Repetir el nombre de la app parece
  redundante, pero el buscador de comandos (Ctrl+K) lista los menús sin su
  aplicación, y ahí «Cuentas Bancarias» se confunde con las de Contactos y
  Contabilidad. Antes solo se sabía que era de Pabilo al abrir el diálogo.

## 16.0.2.4.0 / 17.0.2.4.0 — un método de pago (y un diario) por cuenta

Un solo método de pago para varias cuentas descuadraba la contabilidad. El
cajero elegía en un popup a qué cuenta había llegado el dinero, pero el
asiento iba al **diario fijo del método**: un cobro que entró al BDV terminaba
asentado en el de Binance. Se verificaba bien y se contabilizaba mal.

- **Ajustes → Pabilo → Crear Métodos de Pago** hace un `pos.payment.method` por
  cada cuenta sincronizada —`Pabilo - Mi cuenta binance`— con **su propio
  diario de banco**. Elegir el método pasa a ser elegir la cuenta, y las dos
  cosas dejan de poder separarse.
- **El POS ya no pregunta a qué cuenta llegó el pago** cuando el método trae la
  suya. El selector queda solo para un método sin cuenta configurada.
- **Los diarios se crean sin moneda**, o sea en la de la compañía.
  `pos.config._check_payment_method_ids` rechaza un método cuyo diario tenga otra
  moneda que el TPV, así que un diario en bolívares no se podría ni agregar a una
  caja en dólares. Y es lo correcto: `pos.payment.amount` está en moneda del TPV;
  los bolívares solo sirven para buscar el movimiento en el banco.
- **No se pisa nada de lo que ya exista.** El botón solo crea lo que falta, así
  que los renombres del cliente sobreviven sin necesidad de ninguna marca —y de
  paso se esquiva `_is_write_forbidden`, que prohíbe escribir en un método con
  sesiones POS abiertas.
- **Sí rellena el diario que falte** en métodos que ya estaban en uso. Sin diario
  Odoo los trata como «pagar después» y el cobro no entra en caja: no es
  configuración del cliente, es un campo vacío y roto.
- **Las cuentas eliminadas en Pabilo archivan su método**, nunca lo borran: hay
  `pos.payment` históricos apuntando ahí.
- **`account.journal.pabilo_user_bank_id`** deja dicho de qué cuenta salió cada
  diario. Hace la operación idempotente y le dice al contable qué banco real hay
  detrás.
- **Migración `16.0.2.4.0`**, conservadora a propósito: no crea métodos ni
  renombra nada —eso es cosa del botón—. Intenta rellenar el diario de los
  métodos en uso y, si hay sesiones abiertas, lo deja dicho en el log con la ruta
  exacta en vez de reventar el arranque.

El asistente **Agregar Método de Pago** también usa ahora el diario de la cuenta
cuando no se le indica uno, para no dejar métodos a medio configurar.

## 16.0.2.3.2 / 17.0.2.3.2 — una sola tasa

- **Fuera `pabilo_alt_rate_field`.** El campo salió de suponer que un módulo de
  moneda alterna llevaría su propia tasa, separada de la de Odoo. Comprobado
  contra uno real —`binaural_rate`, de Binauraldev— no la lleva: su
  `pos.config.foreign_rate` **ni siquiera está guardado**, se calcula al vuelo
  desde `res.currency.rate`. Las dos "fuentes" eran el mismo número por dos
  caminos, así que la perilla solo daba a elegir entre lo mismo, y eso confunde
  más de lo que ayuda.
- **Queda una sola tasa: la nativa de Odoo**, y dos escapes para cuando no sirve,
  que no hay que configurar: el cajero puede escribir otra tasa o el monto exacto
  del comprobante. Para quien de verdad tenga otra fuente sigue estando
  `_pabilo_conversion_rate`, que es una herencia de cinco líneas.
- El origen del monto `alterno` pasa a llamarse `tasa_elegida`, que es lo que es.

## 16.0.2.3.1 / 17.0.2.3.1 — se ve de cuándo es la tasa

- **La etiqueta de la tasa dice su fecha cuando no es de hoy**: «771,07 VES por
  cada USD, del 14/08/2026». `_get_rates` toma la última fila con fecha ≤ hoy, así
  que la tasa vigente puede ser de hace días sin que nadie se entere; en Venezuela
  eso da un monto que no cuadra con el comprobante del cliente. Si la tasa es de
  hoy no se dice nada, para no meter ruido en cada cobro.

Verificado contra el montaje real de un cliente (Odoo 16, `binaural_rate` de
Binauraldev): ese módulo **guarda la tasa en el `res.currency.rate` nativo** y
`pos.config.foreign_rate` es un espejo suyo, así que el comportamiento por defecto
del addon ya da la cifra que el cajero ve en pantalla y no hace falta configurar
`pabilo_alt_rate_field`. El campo queda para cuando las dos se separan.

## 16.0.2.3.0 / 17.0.2.3.0 — elegir la tasa en el POS

La `2.2.0` arreglo que se convirtiera, pero dejo la tasa atada a la contable de
Odoo. En Venezuela la tasa del mostrador no siempre es esa, y con un modulo de
moneda alterna de terceros la pantalla puede estar mostrando una tasa propia. El
monto convertido no coincidia con el comprobante del cliente y Pabilo volvia a
responder `PAYMENT_AMOUNT_NOT_VALID`, ahora por centimos en vez de por un factor.

- **La tasa sigue siendo la nativa de Odoo** (`res.currency.rate`, o sea
  Contabilidad → Configuración → Monedas → Tasas): la misma que usa el resto del
  sistema para facturar y contabilizar. Lo que cambia es que deja de ser la única
  opción.
- **El cajero elige cómo se valida.** Cuando hay conversión de por medio, antes de
  pedir la referencia el POS muestra cuánto se va a buscar en el banco y con qué
  tasa salió, y ofrece tres caminos: aceptarlo, **usar otra tasa** o **escribir el
  monto** tal como aparece en el comprobante del cliente. Si la moneda del POS ya
  es la del banco no hay nada que elegir y no se pregunta, para no gastar un toque
  por cobro.
- **`pabilo_alt_rate_field` en el método de pago**: ruta donde leer, en el POS, la
  tasa de un módulo de moneda alterna que lleve la suya. Si está, se propone esa
  en vez de la de Odoo, que es la que el cliente vio en pantalla. Vacío por
  defecto — sin ella todo sigue con las tasas de Odoo.
- **`_pabilo_conversion_rate` como punto de extensión** para quien prefiera
  resolverlo en Python en vez de por configuración. Devuelve `0.0` cuando Odoo no
  sabe la tasa, en vez de dejar que `_get_rates` responda su `COALESCE(..., 1.0)`
  y el monto pase intacto sin que nadie se entere.
- **El origen del monto queda en el log** (`tasa`, `alterno`, `manual`, `igual`),
  para poder reconstruir después por qué se pidió esa cifra.
- **Los campos de configuración de Pabilo se pueden tocar con la caja abierta.**
  Odoo bloquea escribir en un método de pago con sesiones abiertas porque cambiarlo
  a media sesión descuadra el cierre; estos no entran en ningún asiento, así que
  obligar a cerrar la caja para corregir una tasa mal escrita no tenía sentido.

Que el monto lo proponga el navegador no abre ningún hueco: el cajero ya decide el
monto de la línea, y quien valida de verdad es Pabilo contra el movimiento real del
banco. Un número equivocado se rechaza.

## 16.0.2.2.0 / 17.0.2.2.0 — multi-moneda en el POS

- **El monto se convierte a la moneda del banco antes de verificar.** Con
  multi-moneda el POS mandaba a Pabilo el monto de la línea tal cual: en una
  venta de 0,60 $ pagada con 36,00 Bs, se le pedía al banco un movimiento de
  0,60 y la respuesta era siempre `PAYMENT_AMOUNT_NOT_VALID` ("monto no
  coincide"). Ahora `pabilo_verify_payment` recibe también la moneda de la línea
  y convierte con la tasa de Odoo (`res.currency._convert`) a la moneda en la que
  el banco registra los movimientos. La conversión vive en el servidor, no en el
  navegador: el POS no tiene las tasas, y así el monto que se verifica no depende
  de lo que diga el cliente.
- **La moneda de cada cuenta sale del proveedor** (`pabilo.user.bank.currency_id`,
  calculado): bolívares en los bancos venezolanos, dólares en Binance Pay. Se
  deduce en vez de guardarse porque la API de Pabilo no devuelve la moneda de la
  cuenta y el espejo local es de solo lectura. Acepta `VEF` si la base no tiene
  `VES`, y busca con `active_test=False`, porque en libros en dólares el bolívar
  suele estar desactivado.
- **Sin tasa de cambio, error explícito en vez de un cobro mal verificado.**
  `_convert` no protesta cuando le falta la tasa: `_get_rates` cae en
  `COALESCE(..., 1.0)` y devuelve el monto intacto, así que el POS habría seguido
  mandando dólares creyendo que eran bolívares. Se comprueba antes y el cajero ve
  `NO_CURRENCY_RATE` con la ruta donde cargarla.
- **El popup de la referencia muestra el monto que se va a buscar en el banco**,
  no el de la línea, que es el que el cajero puede contrastar con el comprobante
  del cliente. Lo calcula el mismo código que luego verifica
  (`pabilo_amount_preview`), así que no pueden discrepar. Si esa consulta falla
  por red, se muestra el monto de la línea y la verificación sigue siendo
  correcta. Un problema de configuración se avisa **antes** de teclear la
  referencia, no después.
- **El monto verificado queda en el recibo**, junto a la referencia y la cuenta.
- **Redondeo a dos decimales siempre**, que es como compara Pabilo, sin depender
  del `rounding` que tengan configurado las monedas en Odoo.

## 16.0.2.1.1 / 17.0.2.1.1 — precio de lista 25 USD

- **El precio del Apps Store queda en 25 USD** (la `2.1.0` salió con 15). Sin
  cambios de comportamiento.

## 16.0.2.1.0 / 17.0.2.1.0 — módulo de pago

Primera versión de pago, publicada en GitHub con el repositorio ya privado.
Para el Apps Store faltan las capturas de la ficha, pasarla a inglés y operar
un cobro en 17 desde el navegador.

- **Módulo de pago: 15 USD y licencia OPL-1.** `price` y `currency` en el
  manifest. La licencia deja de ser LGPL-3 y pasa a la propietaria de Odoo, que
  es la que permite vender sin que el comprador pueda redistribuirlo. Las
  versiones `2.0.0` y `2.0.1` siguen siendo LGPL-3: el cambio rige de aquí en
  adelante. El repositorio pasa a privado, porque el código a la vista hace
  irrelevante cualquier licencia.

## 16.0.2.0.1 / 17.0.2.0.1 — textos de cara al cliente

Sin cambios de comportamiento: solo texto y metadatos.

- **Correo de soporte en el manifest** (`contacto@pabilo.app`). Es lo que muestra
  el Apps Store en la ficha y lo que pide el formulario de publicación.
- **La ayuda de la URL base deja de hablar de Docker.** El texto de Ajustes
  explicaba cómo apuntar a un backend local (`host.docker.internal:3349`), que no
  le sirve a nadie que instale el módulo: ahora dice que por defecto es
  `https://api.pabilo.app` y cuándo tocarla. Igual en el README del addon.
- **`.pot` regenerado en las dos series**, porque los dos mensajes anteriores son
  traducibles. En `16.0`, además, los cuatro mensajes de estado heredados de
  `payment` vuelven a su inglés de origen: aquel export había salido de una base
  con español instalado y se llevó las traducciones al template.

## 16.0.2.0.0 / 17.0.2.0.0 — primera versión publicable

Primera versión apta para el Apps Store. Antes de esto el flujo del POS no podía
funcionar: tenía cuatro fallos independientes en la misma ruta.

### El POS funciona

- **Se registra el terminal de pago.** Faltaba `register_payment_method`, y sin
  eso `payment_terminal` quedaba `undefined`: el POS trataba el método como pago
  manual y `send_payment_request` no se ejecutaba nunca.
- **RPC correcto.** Se usaba `this.rpc`, que no existe en `PaymentInterface`; el
  `TypeError` se mostraba como «no se pudo conectar con Pabilo».
- **Se desanida la respuesta.** El backend devuelve `data.user_bank_payment`; se
  leía de la raíz, así que el estado siempre venía vacío.
- **Se compara contra el estado real.** Se esperaba `verified`, pero el backend
  solo emite `pending | paid | failed`: rechazaba pagos válidos tras haber
  consumido el crédito.

### Flujo del cajero

- Teclado numérico para los **últimos 6 dígitos** de la referencia: el backend
  compara por sufijo, así que no hace falta teclear la referencia completa.
- **Fecha del pago prellenada con hoy.** Acota la búsqueda al día y en el caso
  normal solo se confirma. Se calcula con la fecha local, no con `toISOString()`,
  que da UTC y de noche cae en el día siguiente.
- Selección de la cuenta bancaria destino cuando hay varias.
- **Una sola consulta con 110 s de timeout**, sin reintentos. Un fallo es
  definitivo: el backend ya consultó el banco antes de responder.
- Los tres fallos se presentan igual, con el motivo real: *pago no encontrado*,
  *pago ya registrado*, *el monto no coincide*.
- **Referencia e ID de Pabilo persistidos** en `pos.payment` y en el recibo.
  Antes se asignaban a un objeto JS que nunca se serializaba: se perdían.
- Se envía `source_name` con la caja y el cajero.

### Seguridad

- **Webhook con firma verificada.** Estaba roto y abierto: usaba
  `request.jsonrequest` (inexistente desde v16) y `type='json'` con JSON-RPC
  mientras Pabilo postea JSON plano, y como se tragaba los errores respondiendo
  200, Pabilo daba la entrega por buena. Sin firma, cualquiera que conociera la
  URL podía marcar un pago como cobrado. Ahora valida HMAC-SHA256 con `consteq`,
  ventana de 5 minutos contra repetición, y rechaza si no hay secreto.
- El secreto es **por cuenta de Pabilo**, no global, y lo trae la sincronización
  automáticamente. Uno compartido permitiría que un comercio firmara webhooks a
  nombre de otro.
- `res.company.pabilo_api_key` restringido a `base.group_system`. Cualquier
  usuario interno podía leerlo por RPC.
- ACL de `payment.transaction` acotada: `write`/`create` ya no son de todo
  usuario interno, que combinado con `_set_done()` permitía dar por pagada una
  transacción.
- `pabilo_verify_payment` exige `point_of_sale.group_pos_user`.

### Cuentas bancarias de solo lectura

- Son un espejo de Pabilo. Editar en Odoo solo producía divergencia silenciosa:
  la siguiente sincronización sobrescribía el cambio mientras el POS verificaba
  contra una cuenta distinta de la que mostraba la pantalla.
- Tres capas, porque la ACL sola la salta cualquier `sudo()`: ACL sin
  `write`/`create`/`unlink` para ningún grupo, guardas que exigen el contexto
  `pabilo_sync`, y vistas sin crear/editar/borrar.
- Lo que Pabilo deja de devolver se marca `is_trashed`, ya que nadie puede
  limpiarlo a mano.

### Sincronización

- **Se refresca sola** si el espejo está vacío o tiene más de una hora, al abrir
  el asistente y al pedir las cuentas desde el POS, más un cron diario. Tener que
  pulsar un botón era una fuente silenciosa de errores: una cuenta nueva no
  aparecía y el cajero verificaba contra la equivocada.
- El botón manual se queda, ahora solo para forzar.

### Configuración

- URL base del backend en el parámetro `pabilo.api_url`; antes estaba
  hardcodeada en tres archivos y no se podía apuntar a otro entorno.
- Menú **Pabilo → Agregar Método de Pago**. El botón que había en la lista de
  métodos de pago no era visible en ninguna de las dos series: los botones de
  `<header>` solo aparecen con filas seleccionadas, y un botón que crea un
  registro no tiene nada que seleccionar.
- El método se llama **Pabilo**, no «Pago Móvil»: el mismo método verifica
  transferencias y Binance.
- Enlaces de pago: se envían `type` y `payment_link_origin`, obligatorios en el
  backend. Sin ellos el botón devolvía 400 siempre.

### Empaquetado

- Manifest con versión de serie, `images`, `maintainer` y descripción larga.
- Ficha de tienda (`index.html`), banner, README y 180 mensajes traducibles.
- 15 `.pyc` sacados del control de versiones, más `.gitignore`.

### Pendiente

- `source_name` no se persiste hasta que el backend acepte el campo.
- Los `msgid` están en español porque el código lo está.
- Falta el correo de soporte en el manifest y capturas del POS en la ficha.
- El POS de 17.0 no se ha operado en un navegador.
