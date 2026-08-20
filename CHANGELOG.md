# Changelog

Formato: `<serie>.<mayor>.<menor>.<parche>`, como exige el manifest de Odoo. Los
tres últimos números van **iguales en las dos ramas**, así que `2.0.0` significa
lo mismo en `16.0` y en `17.0`.

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
