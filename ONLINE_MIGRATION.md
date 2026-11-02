# Leads Google — plan de migración privada

Rama: `develop/online-private`. No fusionar con `main` ni desplegar públicamente antes de superar las pruebas.

## Objetivos
- Conservar la aplicación local existente sin modificaciones.
- Autenticación individual con Supabase Auth; ninguna API de contactos o trabajos accesible sin sesión válida.
- PostgreSQL en Supabase para contactos y trabajos; migración reproducible desde SQLite y copia de seguridad previa.
- Render para el servidor Python con HTTPS; secretos solo en variables de entorno.
- Scraper: ejecutar mediante un trabajador compatible con el entorno de despliegue, sin asumir disponibilidad de Docker dentro de Render.
- Evitar exponer datos personales, claves administrativas o tokens en el frontend.

## Puertas de calidad
1. Pruebas unitarias actuales en Linux y Windows.
2. Pruebas de autenticación: anónimo rechazado, sesión válida aceptada, token expirado rechazado.
3. Importación CSV/JSON, deduplicación, notas, estados, filtros, exportación y recuperación de datos.
4. Pruebas de migración SQLite → PostgreSQL, incluyendo caracteres Unicode y reinicios.
5. Pruebas del scraper con simulación y una extracción real controlada.
6. Pruebas de seguridad: acceso cruzado, CORS, límites de entrada y manejo de errores.
7. Pruebas de despliegue privado en staging, registros y recuperación ante fallos.

## Estado
- [x] Rama independiente creada.
- [x] Revisión inicial de README, app.py, index.html y tests/test_app.py.
- [ ] Implementación de autenticación y almacenamiento.
- [ ] Ejecución de pruebas ampliadas.
- [ ] Configuración de Supabase y Render.
- [ ] Aprobación explícita antes de abrir acceso externo.

No introducir secretos reales en el repositorio.
