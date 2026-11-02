# Añadir un idioma o corregir una traducción

No hace falta tocar el programa ni saber programar: cada idioma es un archivo JSON.

## Crear una traducción

1. Abre **Ajustes → Preferencias → General** y pulsa **Crear traducción**. Se crea `plantilla.json` en la carpeta de idiomas y se abre esa carpeta.
2. Cambia en el archivo `name` por el nombre del idioma, tal como se escribe en ese idioma, y `code` por su código:

   ```json
   {
     "name": "Français",
     "code": "fr",
     "strings": {
       "Abrir": "Ouvrir",
       "No existe: {src}": "Introuvable : {src}"
     }
   }
   ```

3. Escribe cada traducción a la derecha de su texto en español. Puedes dejar textos vacíos: se mostrarán en español.
4. Vuelve a abrir las preferencias, elige el idioma y reinicia el programa cuando te lo pida.

## Reglas

- **No cambies la parte izquierda:** el texto en español es la clave con la que el programa busca la traducción.
- **Conserva las marcas entre llaves**, como `{src}` o `{count}`: ahí va un dato. Se pueden mover de sitio dentro de la frase. Si una marca no coincide, se muestra el texto en español en lugar de fallar.
- **`&` indica la tecla de acceso** de un menú, como en `&Archivo`. Procura que no se repita la misma letra dentro de un menú.
- **Las opciones de un mismo menú de clic derecho deben ser distintas entre sí**: el programa las reconoce por su texto.
- **El código:** usa el código de dos letras del idioma (`fr`) o, para una variante, idioma y país (`pt_BR`). Con el código adecuado, la opción «Automático» lo elige sola según el idioma del sistema.

## Dónde van los archivos

| Carpeta | Para qué |
|---|---|
| `%APPDATA%\DirectoryCommanderTuxTux\languages` en Windows, `~/.config/directory-commander-tux-tux/languages` en Linux | Tus idiomas y correcciones. Tiene prioridad: un archivo con el código `en` sustituye al inglés que trae el programa |
| `directory_commander_tux_tux/languages` en el código | Los idiomas que se publican con el programa |

## Para quien programa

- Todo texto visible se escribe en español y se marca con `tr("Texto")`, o con `tr("Hola {nombre}", nombre=valor)` si lleva datos.
- Lo que además se guarda o se usa como identificador, como las opciones de los menús, los nombres de los estilos o las etiquetas de la barra de herramientas, se marca con `N_("Texto")` y se traduce al mostrarlo con `tr(clave)`.
- El idioma se fija al arrancar, antes de importar la interfaz. Por eso cambiarlo requiere reiniciar.
- `tests/test_i18n.py` falla si un texto marcado no está en `languages/en.json`, si sobra alguno o si una traducción cambia las marcas. También recorre la interfaz real en inglés en busca de lo que haya quedado en español.
