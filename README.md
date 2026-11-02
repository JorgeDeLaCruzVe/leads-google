# Leads Local

Panel local en español para organizar resultados de gosom/google-maps-scraper y preparar importaciones al CRM.

## Abrir

Con Python 3 instalado, haz doble clic en `iniciar.cmd`. Abre http://127.0.0.1:8765. Mantén la ventana del servidor abierta. Para extraer datos, abre primero Docker Desktop y espera a que arranque su motor Linux. La primera búsqueda descarga la imagen oficial `gosom/google-maps-scraper` y sus dependencias si no existen.

## Instalar en otro PC o modificar el código

Requisitos: Python 3.10 o posterior. No hay paquetes Python adicionales. Para las búsquedas se requiere Docker con contenedores Linux; importar, editar y exportar funciona sin Docker.

```sh
git clone https://github.com/JorgeDeLaCruzVe/leads-google.git
cd leads-google
python app.py --open
```

En Windows también puedes ejecutar `iniciar.cmd`. En Linux/macOS usa `sh iniciar.sh` o `python3 app.py --open`. El repositorio es privado: inicia sesión con una cuenta autorizada para clonarlo. Para actualizar, detén la app y ejecuta `git pull`.

Puedes modificar `app.py` (servidor y datos) e `index.html` (interfaz y exportación). Reinicia el servidor tras cambiar Python y recarga el navegador tras cambiar la interfaz.

Para trasladar tus contactos a otra instalación, exporta JSON e impórtalo allí; conserva las notas, estados y campos originales. Otra opción es copiar `data/` con ambos servidores detenidos. La carpeta de datos nunca se sube a GitHub.

Variables opcionales: `LEADS_PORT` cambia el puerto local y `LEADS_DATA_DIR` el directorio de almacenamiento. Por defecto son `8765` y `data/` junto al código.

## Pruebas

```sh
python -m unittest discover -s tests -v
```

Las pruebas usan bases temporales aisladas y comprueban importación, duplicados, restauración JSON, notas, validación y errores de Docker. La prueba del proceso de extracción usa un resultado simulado; no equivale a comprobar Google Maps en vivo. GitHub Actions ejecuta estas pruebas en Windows y Linux con cada push.

## Próximo despliegue online

Subir el código a GitHub no publica la aplicación. Esta versión sigue siendo local y de un solo usuario. Para alojarla online hace falta un servidor con almacenamiento persistente y capacidad de ejecutar el scraper (por ejemplo, Docker en un VPS), además de autenticación, HTTPS, un servidor de aplicación apto para producción y gestión de trabajos. GitHub Pages no ejecuta Python ni Docker.

No cambies la escucha a `0.0.0.0` ni publiques el puerto actual directamente: los contactos y los controles no tienen autenticación. El alojamiento y el dominio se deben configurar como una siguiente fase. No se ha desplegado esta versión en Internet.

## Flujo

1. Introduce actividad y ciudad o importa un CSV/JSON del scraper. CSV admite coma, punto y coma o tabulador; JSON admite un array de objetos. Se requiere `title` para importar una fila.
2. Filtra los contactos, abre sus fichas y guarda estado y notas.
3. Selecciona contactos o exporta los resultados del filtro actual. En exportación puedes cambiar los encabezados y elegir el separador. La selección persiste al cambiar filtros.
4. Importa el CSV en tu CRM usando su asistente. No hay envío por API al CRM todavía.

Se conservan todos los campos recibidos. Se combina por place_id, cid, enlace de Google Maps o nombre y dirección, en ese orden. Las notas y estados locales se mantienen al reimportar. Diferentes fuentes que no compartan esos identificadores pueden requerir revisión manual. Los correos solo aparecen cuando el scraper consigue encontrarlos.

Los datos están en `data/leads.db`; los CSV originales y logs de búsqueda en `data/<id>/`. Copia la carpeta data con el panel detenido para realizar un respaldo. La app escucha únicamente en este equipo. No incluye autenticación ni está preparada para exponerla en Internet.

Los CSV protegen valores que comienzan por caracteres de fórmula mediante un apóstrofo, incluidos teléfonos con prefijo +. JSON conserva los valores originales. Comprueba cómo trata esos valores el importador de tu CRM.

Si el servidor se cierra durante una extracción, el trabajo se marca como interrumpido al reiniciar. Puede que el contenedor siga ejecutándose; revisa Docker Desktop y los archivos de resultados antes de repetirlo. Puedes importar manualmente el CSV recuperado.

Motor externo: https://github.com/gosom/google-maps-scraper (MIT). Este panel utiliza la imagen oficial y no contiene una copia del código del scraper.
