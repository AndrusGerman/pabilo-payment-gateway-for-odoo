# Publicación — pabilo_payment_gateway

Guía para subir el módulo al Odoo Apps Store. Las diferencias entre series y el
procedimiento de pruebas están en [VERSIONES.md](VERSIONES.md); el historial de
cambios en [CHANGELOG.md](CHANGELOG.md).

## Modelo de ramas

**No hay `main`.** Una rama por serie, nombrada igual que la versión de Odoo:

```
16.0     ← rama de corrección: los arreglos entran aquí primero
17.0     ← rama por defecto en GitHub: la serie más nueva
```

Es la convención de `odoo/odoo` y de la OCA, y aquí no es dogma: el código es
específico de la serie. El JS del POS de 16 y 17 no comparte una línea y la vista
de ajustes tampoco, así que un `main` sería o un duplicado de una serie, o una
fusión que no arranca en ninguna.

Son **dos papeles distintos y no tienen por qué coincidir**:

- **Rama por defecto (`17.0`).** Lo que ve quien llega al repositorio y la base de
  los PR. Es la serie más nueva, que es lo que va a instalar quien empiece hoy.
  Cambia solo cuando se publique una serie posterior.
- **Rama de corrección (`16.0`).** Donde entran los arreglos primero, porque 16 es
  lo que está en producción y donde se valida contra el uso real. De ahí se llevan
  a `17.0` con `cherry-pick`. Cuando producción migre a 17, se invierte el sentido
  del port y `16.0` pasa a mantenimiento.

Lo que nunca se cherry-pickea sin revisar: `static/src/js/*`,
`views/res_config_settings_views.xml` y cualquier vista con condiciones de
visibilidad.

## Numeración

El manifest de Odoo exige el prefijo de serie: `<serie>.<mayor>.<menor>.<parche>`.
Sin él, el Apps Store no puede comparar versiones.

```
16.0.2.0.0    17.0.2.0.0    ← actual
16.0.2.1.0    17.0.2.1.0    ← funcionalidad nueva
16.0.2.1.1    17.0.2.1.1    ← arreglo
```

Los **tres últimos números van iguales en las dos ramas**: así `2.1.0` significa
lo mismo en ambas y se ve de un vistazo si están parejas.

## Paquetes

Se generan solo con archivos versionados, así que no arrastran `__pycache__`,
scripts de prueba ni secretos:

```bash
mkdir -p dist
for b in 16.0 17.0; do
  v=$(git show $b:addons/pabilo_payment_gateway/__manifest__.py \
      | python -c "import ast,sys; print(ast.literal_eval(sys.stdin.read())['version'])")
  git archive --format=zip --prefix=pabilo_payment_gateway/ \
      "$b:addons/pabilo_payment_gateway" -o "dist/pabilo_payment_gateway-${v}.zip"
done
```

| Paquete | Rama | Etiqueta |
| :--- | :--- | :--- |
| `pabilo_payment_gateway-16.0.2.1.0.zip` | `16.0` | `v16.0.2.1.0` |
| `pabilo_payment_gateway-17.0.2.1.0.zip` | `17.0` | `v17.0.2.1.0` |

33 archivos cada uno, contenido idéntico al árbol versionado que se probó
instalando. `dist/` está en `.gitignore`.

## Lo que ya está verificado

Ejecutado contra `odoo:16.0` y `odoo:17.0` reales, no inferido:

| | 16.0 | 17.0 |
| :--- | :--: | :--: |
| Instala limpio en base nueva | ✅ | ✅ |
| Desinstala sin residuos (`ir.model`, columnas) | ✅ | ✅ |
| Cuentas de solo lectura: guardas y ACL | ✅ | ✅ |
| `appKey` restringido a `base.group_system` | ✅ | ✅ |
| Terminal registrado en `use_payment_terminal` | ✅ | ✅ |
| Campos Pabilo cargados al front-end del POS | ✅ | ✅ |
| `_payment_fields` persiste la referencia | ✅ | ✅ |
| Rutas de import ES6 y props de los popups | n/a | ✅ |
| Traducciones exportables (180/181 mensajes) | ✅ | ✅ |
| Sincronización real + secreto de webhook automático | ✅ | ✅ |
| Webhook: firma, repetición, cuerpo alterado, status | ✅ | ✅ |
| Sin secretos ni `.pyc` en archivos versionados | ✅ | ✅ |

Detalle del webhook, contra el Odoo real con la firma calculada aparte:

```
sin firma / inválida / vieja / cuerpo alterado  -> 401
status desconocido                             -> 400
enlace inexistente                             -> 404
firma correcta                                 -> 200, tx en done/verified/paid
```

## Pendiente antes de subir

### Bloqueantes

1. **Correo de soporte** en el manifest. Hay un `TODO` con la clave `support`
   comentada; el Apps Store lo muestra en la ficha.
2. **Capturas de pantalla del POS.** La ficha (`static/description/index.html`)
   solo tiene el banner. Faltan: el teclado numérico de la referencia, la
   confirmación de un pago verificado y la pantalla de Ajustes.
3. **Abrir el POS en 17.0 y hacer un cobro real.** Todo lo verificable sin
   navegador está probado, pero nadie ha cobrado en esa serie: los popups y el
   estado `waitingCard` solo se ven abriendo la pantalla de pago.

### Decisiones tuyas

4. **Idioma de origen.** Los `msgid` están en español porque el código lo está.
   Para Venezuela sirve; para una ficha internacional habría que pasar el fuente
   a inglés y dejar un `es.po`.
5. **`source_name` no se persiste.** Odoo manda la caja y el cajero en el
   payload, pero el backend no tiene el campo en `PaymentInput`, así que lo
   ignora. Cuando lo acepte, se guarda sin tocar nada del lado de Odoo.

## Notas de despliegue

**El `dbfilter` debe resolver a una sola base.** Si coincide con varias, Odoo no
sabe cuál usar en una ruta pública y `/pabilo/webhook` responde 404 en lugar de
procesar el webhook. Se comprobó en los dos sentidos: con dos bases visibles da
404; con una, 401 por falta de firma (la ruta vive).

**El secreto del webhook se obtiene solo.** Al sincronizar cuentas, Odoo consulta
`GET /me/webhook-secret` y lo guarda. Es propio de cada cuenta de Pabilo, no
compartido entre comercios. Si no hay secreto, el webhook rechaza todo en vez de
aceptar a ciegas.

**Sin reintentos.** Un fallo de verificación es definitivo: el backend ya
consultó el banco. Una sola llamada con 110 s de timeout en el servidor y 115 s
en el navegador, para que gane el mensaje real de Pabilo sobre un error de red
genérico.

## Publicar

1. Entrar a https://apps.odoo.com con la cuenta del autor.
2. Subir un paquete **por serie** — son módulos distintos en la tienda, no
   versiones del mismo.
3. La ficha se toma de `static/description/index.html`; el icono de
   `static/description/icon.png` (256×256) y el banner de `images` en el
   manifest.
4. Tras la aprobación, mover la etiqueta a lo publicado si hubo cambios.

## Al liberar una versión nueva

1. Corregir en `16.0` y llevar a `17.0` con `cherry-pick`. Lo que no se puede
   portar tal cual: `static/src/js/*`, la vista de ajustes y las condiciones de
   visibilidad de las vistas.
2. Subir el último número del manifest en las dos ramas.
3. Regenerar el `.pot` (comando en VERSIONES.md).
4. Correr la suite de comprobaciones de VERSIONES.md en ambas.
5. Reconstruir los zips, etiquetar y subir.
