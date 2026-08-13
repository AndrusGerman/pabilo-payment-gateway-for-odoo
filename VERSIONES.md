# Versiones y pruebas — pabilo_payment_gateway

Este repositorio mantiene **una rama por serie de Odoo**, que es la convención de
Odoo y lo que espera el Apps Store: se publica un módulo distinto por versión, no
un único paquete que soporte varias.

| Rama | Odoo | Versión del manifest | Imagen de pruebas |
| :--- | :--- | :--- | :--- |
| `16.0` | 16.0 | `16.0.2.0.0` | `odoo:16.0` |
| `17.0` | 17.0 | `17.0.2.0.0` | `odoo:17.0` |
| `main` | — | — | Queda en el commit previo a este trabajo |

`docker-compose.yml` está fijado a la imagen que corresponde en cada rama, así que
basta con hacer checkout para tener el entorno correcto.

## Por qué ramas separadas y no un solo código

El front-end del POS cambió por completo entre 16 y 17: de módulos legacy
(`odoo.define` + `require`) a módulos ES6 con OWL. No hay forma razonable de que
un mismo archivo JS cargue en ambas. El resto del módulo —modelos, cliente HTTP,
ACL, contrato con el backend— es idéntico, así que **las correcciones de lógica
se hacen en `16.0` y se llevan a `17.0` con `git cherry-pick`**.

Lo que no se puede cherry-pickear sin revisar: `static/src/js/*`,
`views/res_config_settings_views.xml` y cualquier vista con condiciones de
visibilidad.

---

## Diferencias entre las dos ramas

Todo lo de esta tabla está verificado contra el código de las imágenes
`odoo:16.0` y `odoo:17.0`, no de memoria.

### 1. Bundle de assets

```python
# 16.0
'assets': {'point_of_sale.assets': [...]}
# 17.0
'assets': {'point_of_sale._assets_pos': [...]}
```

Referencia: `point_of_sale/__manifest__.py:81` en v17.

### 2. Módulos JS

| | 16.0 | 17.0 |
| :--- | :--- | :--- |
| Declaración | `odoo.define('...', function (require) {...})` | `/** @odoo-module */` + `import` |
| PaymentInterface | `require('point_of_sale.PaymentInterface')` | `@point_of_sale/app/payment/payment_interface` |
| Registro | `require('point_of_sale.models').register_payment_method` | `@point_of_sale/app/store/pos_store` |
| Modelo `Payment` | `Registries.Model.extend(Payment, ...)` | `patch(Payment.prototype, {...})` de `@web/core/utils/patch` |
| Clase | `PaymentInterface.extend({ init() {...} })` | `class X extends PaymentInterface { setup() {...} }` |
| RPC | `require('web.rpc').query({model, method, args})` | `this.env.services.orm.silent.call(model, method, args)` |
| Popups | `Gui.showPopup('NumberPopup', {...})` | `this.env.services.popup.add(NumberPopup, {...})` |
| Traducción | `require('web.core')._t` | `@web/core/l10n/translation` |
| Interpolación | `_.str.sprintf(_t('%s'), v)` | `_t('%s', v)` — `_t` ya interpola |

El id de módulo que genera Odoo para un archivo en `static/src/js/x.js` es
`@pabilo_payment_gateway/js/x`. Se puede comprobar sin abrir el navegador:

```python
from odoo.tools.js_transpiler import url_to_module_path
url_to_module_path('/pabilo_payment_gateway/static/src/js/payment_pabilo.js')
```

### 3. Vistas: `attrs` desaparece

```xml
<!-- 16.0 -->
<field name="pabilo_user_bank_id"
       attrs="{'invisible': [('use_payment_terminal', '!=', 'pabilo')],
               'required':  [('use_payment_terminal', '=',  'pabilo')]}"/>
<!-- 17.0 -->
<field name="pabilo_user_bank_id"
       invisible="use_payment_terminal != 'pabilo'"
       required="use_payment_terminal == 'pabilo'"/>
```

### 4. Vista de Ajustes: estructura nueva

En v17 `base.res_config_settings_view_form` es un `<form/>` **vacío**: ya no
existe el `div.settings` sobre el que hacía xpath la v16. Se usan las etiquetas
`<app>` / `<block>` / `<setting>`.

```xml
<!-- 16.0 -->
<xpath expr="//div[hasclass('settings')]" position="inside">
    <div class="app_settings_block" string="Pabilo Payment" data-key="pabilo_payment_gateway">
<!-- 17.0 -->
<xpath expr="//form" position="inside">
    <app data-string="Pabilo" string="Pabilo" name="pabilo_payment_gateway" groups="base.group_system">
        <block title="Conexión con Pabilo" id="pabilo_api_section">
            <setting string="API Key (appKey)" help="...">
```

### 5. Lo que NO cambia

Verificado en la imagen de v17, aunque se esperaba lo contrario:

- **`_lt` sigue existiendo** (`odoo/__init__.py:129`). El mapa de errores de
  `pabilo_client.py` no necesita cambios. (Se sustituye por `LazyTranslate` en
  versiones posteriores, no en la 17.)
- **`_loader_params_pos_payment_method`** sigue en `pos.session`
  (`pos_session.py:2123`). El override de `pos_session.py` porta tal cual.
- **`_payment_fields`** sigue en `pos.order` (`pos_order.py:66`).
- `_get_payment_terminal_selection` y `_is_write_forbidden` en
  `pos.payment.method`; `payment.provider.code` como Selection; `selected_paymentline`
  en el modelo `Order` del POS.
- `<header>` dentro de `<tree>` es válido en **ambas** (`ir_ui_view.py:1457` en
  v16), así que el botón "Agregar Método Pabilo" funciona igual.

Resultado: **todo el Python porta sin tocar una línea.** Solo cambian manifest,
los dos archivos JS y cuatro XML.

---

## Cómo probar

Requiere Docker. No hace falta el backend de Pabilo para las pruebas de esta
sección: verifican el módulo, no la integración.

### Instalación limpia

Es la prueba que replica lo que hace un revisor del Apps Store o un cliente nuevo.

```powershell
git checkout 16.0        # o 17.0
docker compose up -d db
docker compose run --rm web odoo -d test_limpio -i pabilo_payment_gateway `
    --stop-after-init --without-demo=all --log-level=warn
```

Debe terminar con código 0. Comprobar el estado:

```powershell
docker compose exec -T db psql -U odoo -d test_limpio `
  -c "SELECT name, state, latest_version FROM ir_module_module WHERE name='pabilo_payment_gateway';"
```

Esperado: `installed` y la versión de la rama (`16.0.2.0.0` / `17.0.2.0.0`).

### Suite de comprobaciones funcionales

Guardar como `addons/_t.py` (la carpeta `addons/` está montada en el contenedor)
y ejecutar:

```powershell
docker compose run --rm -T --entrypoint bash web `
  -c "odoo shell -d test_limpio --db_host=db --db_user=odoo --db_password=odoo --no-http --log-level=error < /mnt/extra-addons/_t.py"
```

> **No** pasar el script por una tubería de PowerShell: le añade un BOM y el
> intérprete falla con `SyntaxError: invalid non-printable character U+FEFF`.
> Hay que redirigirlo **dentro** del contenedor, como arriba.

```python
Bank = env['pabilo.user.bank']

# 1. Las cuentas son de solo lectura fuera de la sincronización
try:
    Bank.create({'name': 'manual', 'pabilo_id': 'x'}); print("FAIL")
except Exception as e:
    print("OK create bloqueado ->", type(e).__name__)

rec = Bank.with_context(pabilo_sync=True).create({
    'name': 'BDV Demo', 'pabilo_id': 'abc123', 'provider': 'VE_BAN',
    'account_number': '01020656110100004041'})
print("OK create con sync:", rec.display_name)

# OJO: `rec` hereda el contexto pabilo_sync del create. Para probar las guardas
# hay que partir de un recordset limpio, o la prueba pasa por el motivo equivocado.
clean = Bank.browse(rec.id)
for op, fn in (('write', lambda: clean.write({'name': 'x'})), ('unlink', clean.unlink)):
    try:
        fn(); print("FAIL: %s permitido" % op)
    except Exception as e:
        print("OK %s bloqueado ->" % op, type(e).__name__)

# 2. La ACL no concede escritura ni al admin
Bank_admin = Bank.with_user(env.ref('base.user_admin'))
for op in ('read', 'write', 'create', 'unlink'):
    try:
        Bank_admin.check_access_rights(op); print("   %-7s PERMITIDO" % op)
    except Exception as e:
        print("   %-7s denegado" % op)

# 3. Configuración del módulo
print("appKey groups:", env['res.company']._fields['pabilo_api_key'].groups)
print("terminal:", ('pabilo', 'Pabilo') in env['pos.payment.method']._get_payment_terminal_selection())
print("wizard default:", env['pabilo.payment.method.wizard'].default_get(['name'])['name'])
print("campos al POS:", env['pos.session']._loader_params_pos_payment_method()['search_params']['fields'])
print("persistencia:", {k: v for k, v in env['pos.order']._payment_fields(
    env['pos.order'], {'amount': 10, 'payment_method_id': 1, 'name': 'x',
    'pabilo_reference': '704777', 'pabilo_payment_id': 'p1', 'pabilo_is_new': True}
).items() if k.startswith('pabilo')})

env.cr.rollback()
```

Resultado esperado en ambas ramas:

```
OK create bloqueado -> UserError
OK create con sync: BDV Personal - BDV Demo - (4041)
OK write bloqueado -> UserError
OK unlink bloqueado -> UserError
   read    PERMITIDO
   write   denegado
   create  denegado
   unlink  denegado
appKey groups: base.group_system
terminal: True
wizard default: Pabilo
campos al POS: [..., 'pabilo_user_bank_id', 'pabilo_account_hint']
persistencia: {'pabilo_reference': '704777', 'pabilo_payment_id': 'p1', 'pabilo_is_new': True}
```

### Rutas de import del JS (solo 17.0)

Los errores de import ES6 no aparecen al instalar: solo revientan cuando el POS
carga en el navegador. Se pueden verificar antes:

```python
from odoo.tools.js_transpiler import transpile_javascript, url_to_module_path
import re
for f in ('payment_pabilo.js', 'models.js'):
    url = '/pabilo_payment_gateway/static/src/js/' + f
    src = open('/mnt/extra-addons/pabilo_payment_gateway/static/src/js/' + f, encoding='utf-8').read()
    print(f, '->', url_to_module_path(url))
    print('   ', sorted(set(re.findall(r'require\("([^"]+)"\)', transpile_javascript(url, src)))))
```

Esperado en 17.0:

```
payment_pabilo.js -> @pabilo_payment_gateway/js/payment_pabilo
    ['@point_of_sale/app/errors/popups/error_popup',
     '@point_of_sale/app/payment/payment_interface',
     '@point_of_sale/app/utils/input_popups/number_popup',
     '@point_of_sale/app/utils/input_popups/selection_popup',
     '@web/core/l10n/translation']
models.js -> @pabilo_payment_gateway/js/models
    ['@pabilo_payment_gateway/js/payment_pabilo', '@point_of_sale/app/store/models',
     '@point_of_sale/app/store/pos_store', '@web/core/utils/patch']
```

Cada ruta debe existir en la imagen:

```powershell
docker run --rm --entrypoint find odoo:17.0 `
  /usr/lib/python3/dist-packages/odoo/addons/point_of_sale/static/src -name "number_popup.js"
```

### Desinstalación limpia

Causa habitual de rechazo en el Apps Store.

```python
mod = env['ir.module.module'].search([('name', '=', 'pabilo_payment_gateway')])
mod.button_immediate_uninstall()
print("estado:", env['ir.module.module'].search([('name','=','pabilo_payment_gateway')]).state)
print("ir.model residual:", env['ir.model'].search_count([('model','=','pabilo.user.bank')]))
env.cr.execute("SELECT column_name FROM information_schema.columns "
               "WHERE table_name='pos_payment' AND column_name LIKE 'pabilo%'")
print("columnas huerfanas:", env.cr.fetchall())
```

Esperado: `uninstalled`, `0`, `[]`.

### Traducciones

```powershell
docker compose run --rm -T --entrypoint bash web `
  -c "odoo -d test_limpio --db_host=db --db_user=odoo --db_password=odoo -u pabilo_payment_gateway --i18n-export=/mnt/extra-addons/pabilo_payment_gateway/i18n/pabilo_payment_gateway.pot --modules=pabilo_payment_gateway --stop-after-init"
```

Deben salir ~170 mensajes, con 18 referencias a `pabilo_client.py` (el mapa de
errores en `_lt`) y 14 a `payment_pabilo.js`. Si las del JS bajan a 0, el
transpilador no está reconociendo el archivo.

### Limpieza

```powershell
docker compose exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS test_limpio;"
docker compose down
```

### Prueba de extremo a extremo con el POS

Requiere el backend de Pabilo. Las credenciales y los IDs de cuentas están en
`CREDENCIALES.local.md` (no versionado).

1. Backend con `HTTP_HOST=0.0.0.0` — con `localhost` no acepta conexiones desde
   el contenedor.
2. `docker compose up -d`, entrar a Odoo, **Ajustes → Pabilo**: appKey y URL base
   `http://host.docker.internal:3349`. En Podman/WSL2 ese nombre apunta al
   gateway de la VM, no al host Windows; por eso `docker-compose.yml` lo ancla a
   la IP LAN con `extra_hosts` — **hay que actualizar esa IP al cambiar de red**.
3. **Sincronizar Cuentas** → deben aparecer las cuentas, en modo solo lectura.
4. **Punto de Venta → Métodos de Pago → Agregar Método Pabilo**, elegir cuenta.
5. Inyectar un pago de prueba: `.\scripts\seed-pagomovil.ps1 -Amount 150 -Verify`
   (script no versionado; imprime los 6 dígitos a teclear).
6. En el POS: vender por Bs. 150, método Pabilo, teclear los 6 dígitos.
7. Casos a demostrar: duplicado (misma referencia dos veces → *Pago ya
   registrado*), no encontrado (`999999` → reintenta ~30 s), monto que no
   coincide (`PAYMENT_AMOUNT_NOT_VALID`).
8. Tras validar la orden, comprobar la trazabilidad:
   ```powershell
   docker compose exec -T db psql -U odoo -d pabilo `
     -c "SELECT id, amount, pabilo_reference, pabilo_payment_id FROM pos_payment ORDER BY id DESC LIMIT 3;"
   ```

---

## Estado de las pruebas

Ejecutado sobre `odoo:16.0` y `odoo:17.0` reales:

| Prueba | 16.0 | 17.0 |
| :--- | :--: | :--: |
| Instalación limpia en base nueva | ✅ | ✅ |
| Guardas de solo lectura (create/write/unlink) | ✅ | ✅ |
| ACL sin escritura ni para admin | ✅ | ✅ |
| Campo computado almacenado | ✅ | ✅ |
| appKey restringido a `base.group_system` | ✅ | ✅ |
| Terminal registrado en `use_payment_terminal` | ✅ | ✅ |
| Campos Pabilo cargados al front-end del POS | ✅ | ✅ |
| `_payment_fields` persiste la referencia | ✅ | ✅ |
| Rutas de import ES6 | n/a | ✅ |
| Exportación de traducciones (170 mensajes) | ✅ | ✅ |
| Desinstalación sin residuos | ✅ | ✅ |

**No probado en navegador.** El flujo del POS (popups, reintentos, cancelación)
está verificado a nivel de contrato —módulos, rutas de import, props de los
popups y firma de los métodos, todo contra el código de cada imagen— pero no se
ha abierto una sesión de POS en 17.0. Es lo que falta antes de publicar esa rama.

## Pendientes antes de publicar

- Abrir el POS en 17.0 y hacer un cobro real.
- Correo de soporte en el manifest (hay un `TODO`).
- Capturas de pantalla del POS para la ficha de tienda.
- Los `msgid` del `.pot` están en español porque el código fuente lo está. Para
  una ficha internacional habría que pasar el fuente a inglés y dejar un `es.po`.
- ~~Rotar el appKey~~: ya no hace falta. Estuvo commiteado en claro, pero se
  purgó de todo el historial con `git filter-repo` y el repo no tenía remoto, así
  que nunca salió de la máquina. Detalles en `CREDENCIALES.local.md`.
- El webhook `/pabilo/webhook` no es apto para producción en ninguna de las dos
  ramas (ver "Limitaciones conocidas" en el README del addon).
