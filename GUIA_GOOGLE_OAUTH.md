# Conectar el agente a tu correo de Google Workspace (OAuth)

Google ya no deja usar contraseñas con programas externos en Workspace. En su lugar, el agente pide permiso
**una vez** con el botón "Iniciar sesión con Google" y guarda una autorización. Hay que crear una "credencial"
en Google Cloud (gratis, no hace falta tarjeta para esto). Son unos 10 minutos.

Necesitas entrar con **el administrador de tu Workspace** (la cuenta con la que creaste info@tudominio).

## 1. Crear el proyecto

1. Abre https://console.cloud.google.com/ con tu cuenta info@tudominio.
2. Si te pide aceptar condiciones, acepta.
3. Arriba, junto al logo de Google Cloud, pulsa el selector de proyectos → **Proyecto nuevo**.
4. Nombre: `ConFauna agente`. Organización: la de tu dominio. **Crear**. Espera unos segundos y selecciónalo.

## 2. Activar la API de Gmail

1. Menú ☰ → **APIs y servicios** → **Biblioteca**.
2. Busca `Gmail API` → entra → **Habilitar**.

## 3. Pantalla de consentimiento (muy importante: tipo "Interno")

1. Menú ☰ → **APIs y servicios** → **Pantalla de consentimiento de OAuth** (en la versión nueva se llama
   **Google Auth Platform**; pulsa **Comenzar**).
2. Nombre de la aplicación: `ConFauna agente`. Correo de asistencia: tu info@.
3. **Público / Audiencia: Interno.** Esto solo existe en Workspace y es lo que evita que la autorización
   caduque a los 7 días y que Google te pida verificar la aplicación.
4. Correo de contacto: el tuyo. Acepta las condiciones y **Crear**.
5. No hace falta añadir "permisos/ámbitos" aquí: el programa los pide al iniciar sesión.

Si no ves la opción "Interno", la cuenta no está en un Workspace de organización: avísame y lo vemos.

## 4. Crear la credencial

1. **APIs y servicios** → **Credenciales** (o *Clientes* en Google Auth Platform) → **Crear credenciales** →
   **ID de cliente de OAuth**.
2. Tipo de aplicación: **Aplicación de escritorio**. Nombre: `ConFauna PC`. **Crear**.
3. Aparece el **ID de cliente** (acaba en `.apps.googleusercontent.com`). Si también muestra un
   **Secreto del cliente**, cópialo. Si no lo muestra, no pasa nada.

## 5. Rellenar el `.env` en PyCharm

Abre `.env` (si no existe, copia `.env.example`) y deja así estas líneas:

```
EMAIL_USER=info@tudominio.es
EMAIL_AUTH=oauth
GOOGLE_CLIENT_ID=PEGA_AQUI_EL_ID_DE_CLIENTE
GOOGLE_CLIENT_SECRET=PEGA_AQUI_EL_SECRETO_SI_LO_HAY
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
IMAP_HOST=imap.gmail.com
```

La línea `EMAIL_PASSWORD` no hace falta: bórrala o déjala vacía.

## 6. Dar el permiso (una sola vez)

En la pestaña **Terminal** de PyCharm (Alt+F12):

```
python agente.py autorizar
```

Se abre el navegador. Elige **info@tudominio**, acepta el permiso de Gmail y verás "Autorización guardada".
Vuelve a PyCharm. Importante: debes elegir la misma cuenta que pusiste en `EMAIL_USER`; si no, el programa avisa.

## 7. Comprobar que todo funciona (sin enviar nada)

```
python agente.py probar
```

Debe terminar con **"Todo listo para enviar."** Si falla, el mensaje dice qué revisar.

## Cosas que conviene saber

- `data/google_token.json` es una llave de tu correo. No lo compartas ni lo subas a ningún sitio (ya está en `.gitignore`).
- La autorización se puede romper si **cambias la contraseña** de la cuenta, si la revocas o si pasan 6 meses sin
  usarla. El autopiloto lo dice en su registro; se arregla repitiendo `python agente.py autorizar`.
- Si en el paso 3 hubieras elegido "Externo" en vez de "Interno", la autorización caducaría cada 7 días mientras
  la app esté en modo de pruebas. Por eso es importante "Interno".
- Si el administrador de tu Workspace restringe aplicaciones de terceros, ve a admin.google.com → Seguridad →
  Controles de acceso y datos → Controles de API y permite tu aplicación (suele estar libre por defecto).
- Los menús de Google cambian de nombre con frecuencia; si algo no coincide, dime qué ves y lo ajustamos.
