# Changelog

Formato: `<serie>.<mayor>.<menor>.<parche>`, como exige el manifest de Odoo. Los
tres últimos números van **iguales en las dos ramas**, así que `2.0.0` significa
lo mismo en `16.0` y en `17.0`.

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
