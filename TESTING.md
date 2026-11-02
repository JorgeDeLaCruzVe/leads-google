# Verificación de la aplicación — 2 de noviembre de 2026

## Comprobado en Windows

- Cuatro pruebas automáticas completadas con `python -m unittest discover -s tests -v`.
- Importación CSV desde el navegador usando dos negocios ficticios y una base aislada.
- Edición de notas y estado, con persistencia en SQLite.
- Filtro de contactos con correo: devuelve únicamente el registro esperado.
- Descarga real de CSV con encabezado personalizado y comprobación del archivo.
- Descarga real de JSON y comprobación de teléfono con prefijo, caracteres Unicode, notas y estado.
- Importación de JSON en una base vacía conserva los datos exportados.
- Las bases de contactos y los archivos de pruebas quedan excluidos de Git.

## Limitaciones

No se ha completado una extracción real de Google Maps: Docker Desktop no respondió al comprobar el motor en este equipo. La prueba automática del proceso de extracción utiliza un CSV simulado y no demuestra conectividad con Google Maps.

El código incluye un workflow de GitHub Actions para Windows y Linux. Su resultado remoto debe consultarse en la pestaña Actions; las pruebas anteriores son las ejecutadas localmente.

No se ha probado una instalación física en otro PC ni se ha desplegado la aplicación online. Los lanzadores y las instrucciones permiten reproducir la instalación con Python y, para extraer, Docker.
