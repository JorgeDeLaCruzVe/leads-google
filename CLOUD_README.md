# Leads Google Cloud — staging privado

El servidor cloud se ejecuta con `python cloud_app.py` y sirve `cloud_index.html`. La versión local `app.py` / `index.html` permanece intacta.

Variables de entorno requeridas:
- `SUPABASE_URL`: URL HTTPS del proyecto Supabase.
- `SUPABASE_PUBLISHABLE_KEY`: clave **publicable**, nunca service-role.
- `PORT`: puerto del servidor, por defecto 10000.
- `HOST`: interfaz de escucha, por defecto 127.0.0.1; usar 0.0.0.0 únicamente en el servicio Render con HTTPS.

La interfaz permite iniciar sesión con un usuario previamente creado en Supabase Auth. Para uso individual, deshabilitar el registro público de usuarios desde Supabase Auth y crear la cuenta del propietario manualmente. No colocar contraseñas en GitHub.

Limitaciones actuales:
- Scraper cloud deshabilitado de forma intencional: el contenedor de Render no garantiza un daemon Docker anidado.
- El login utiliza un token de acceso en memoria; cuando expire, será necesario volver a iniciar sesión.
- La importación procesa contactos uno a uno y puede requerir optimización para ficheros grandes.
- No desplegar ni usar con datos reales hasta completar pruebas de integración con Supabase y auditoría de seguridad.
- No migrar datos existentes sin copia de seguridad y validación previa.

Pruebas: `python -m unittest discover -s tests -v`.
