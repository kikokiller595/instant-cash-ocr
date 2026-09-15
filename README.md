# Instant Cash OCR Bot

Proyecto listo para servir la web, la API y una vista para OBS desde una sola app Python.

## Rutas

- `/` -> pagina principal
- `/obs` -> misma pagina en modo limpio para Browser Source de OBS
- `/admin` -> panel de historial y logs
- `/remote` -> panel de control de estados
- `/api/states/health` -> healthcheck

## Local

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
python states_controller.py
```

Si usas Windows y Tesseract no esta en la ruta por defecto, define `TESSERACT_CMD`.

## Railway

El repo ya incluye `Dockerfile`, asi que Railway puede desplegarlo directamente como servicio web.

Variables utiles:

- `PORT`: la pone Railway automaticamente
- `HOST=0.0.0.0`
- `DATA_DIR=/data` para guardar `latest.json`, `states.json` y `schedule.log` en un volumen persistente
- `RUN_OCR_SCHEDULER=1` si quieres que el OCR se ejecute solo en segundo plano
- `PLAYWRIGHT_HEADLESS=1` recomendado en Railway
- `DAILY_RESET_ENABLED=1` limpia `latest.json`, `states.json`, `schedule.log` y reinicia el scheduler a las 12:00 AM ET. Viene activo por defecto.
- `DAILY_RESET_RESTART_PROCESS=1` re-ejecuta la app despues del reset de medianoche para liberar procesos/hilos del contenedor. Viene activo por defecto.

Recomendado en Railway:

1. Crea el servicio desde este repo.
2. Monta un volumen persistente en `/data`.
3. Define `DATA_DIR=/data`.
4. Si quieres OCR automatico, define `RUN_OCR_SCHEDULER=1`.

## OBS

En OBS agrega un `Browser Source` apuntando a:

```text
https://TU-DOMINIO.up.railway.app/obs
```

Si prefieres ver tambien el menu manual, usa `/`.

## Notas

- La app sirve los archivos estaticos desde `site/`.
- Los datos de runtime se guardan en `DATA_DIR` si existe; si no, se usan los archivos dentro de `site/`.
- `latest.json` acepta tanto formato arreglo como objeto para mantener compatibilidad con datos viejos.
- El bot lee los resultados directamente del WebSocket de instantcash.bet (`/draw/findAllDrawsByDate`) y guarda esas entradas con `source: "site"`. El OCR de la captura queda solo como respaldo si esa consulta falla.
- Si un sorteo todavia no esta publicado en el sitio, el scheduler reintenta hasta 20 minutos y cada corrida rellena los sorteos del dia que hayan quedado sin guardar.
