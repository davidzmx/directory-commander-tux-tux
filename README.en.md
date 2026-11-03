# Directory Commander ~~Plus Plus~~ Tux Tux

[Español](README.md) | **English**

A **multi-pane file manager with built-in terminals and git**, for **Linux (KDE and GNOME)** and **Windows**. Written in Python with Qt 6 (PySide6).

![Four panes, each with its terminal and the git branch of its folder](docs/capturas/en/principal.png)

## Multi-pane, terminals and git

### Up to eight panes

- From one to eight panes, side by side, one above the other or in a grid, each with its own tabs
- Each pane can show or hide its own folder tree from the button on its bar, just like its terminal
- With more than two panes, each one goes with its terminal and its tree inside a frame of its own color, thicker for the active pane and, if you like, the others dimmed. It is shown and hidden with its toolbar button; from how many panes, the thickness, the dimming and the colors are chosen in Preferences
- Right-clicking an empty area splits a pane, with a new one below or to the right in the same folder, and closes it when it's no longer needed
- Styles to change the layout with one click, which you can edit or create yourself: see [Styles](#styles-one-layout-for-each-task)
- Named sessions that save panes, tabs and terminals. They are loaded, saved and started at the top right, and managed in Preferences → Sessions

### A terminal in each pane

- One terminal per pane, below it or to its right, plus a general one. They are real consoles: interactive programs such as `python`, `ssh` or `vim` work
- Each terminal follows its pane: when you change folder it types the `cd` for you, unless a program is running or you have started typing a command; in that case it waits until you finish. A button on its bar turns this on or off
- Each one can use its own shell, without leaving the folder it's in. On Windows, PowerShell, `cmd`, Git Bash, Cygwin, MSYS2, WSL or another program; on Linux, `bash`, `zsh`, `fish` and any others you have installed. The default is chosen in Preferences → General
- A pane's terminal opens with its button or `Ctrl+Shift+O`, and the general one with the toolbar button or `Ctrl+Shift+G`. Hiding them doesn't end what's running
- A pane can show only its terminal, filling its whole space, from the button on its bar or the right-click menu. It is never the source or the destination, and its terminal follows nobody unless you ask it to, and then it goes with the active pane. Its bar has a button to see the files again, in the folder the terminal is in, and an X that closes the pane. At least one pane with files always remains
- Dropping files on a terminal types their paths, quoted, without running anything
- The «Vertical pane-terminal» and «Horizontal pane-terminal» styles put a terminal in place of the second pane. In Preferences you can also make the general terminal take the whole width of the window when panes are side by side

### Built-in git

- Each terminal whose folder is a repository shows the active branch and the user commits will be signed with, and lets you switch branch or create one. The branch also appears on each pane's bar
- Buttons for `status`, `add`, `unstage`, `commit`, `push`, `pull`, `fetch`, `diff` and `log`, written in that same terminal
- `add`, `unstage` and `commit` open a window with the files and checkboxes, and show the command being built before sending it
- The command of each button can be changed and others can be added, in Preferences → Button bars. The rest is set in Preferences → Git

![The commit window, with the files to include and the command that will run](docs/capturas/en/commit.png)

## Styles: one layout for each task

A style is a way of arranging the window: panes, folder trees, viewer, metadata, view and terminals. You switch between them with one click on the styles bar.

**The ones that come with the program are just a starting point: you can edit, duplicate, rename and delete them, and create your own.** Do it in Preferences → Styles or with the «New style…» button at the end of the bar. The editor shows a diagram of how the window will look, and you choose which styles appear on the bar and in what order.

| | |
|---|---|
| **Commander**: the classic two panes, with several tabs in each<br>[![Commander style](docs/capturas/en/comandante.png)](docs/capturas/en/comandante.png) | **Vertical Dual Tree**: a folder tree for each pane, here in the light theme<br>[![Vertical Dual Tree style](docs/capturas/en/doble-arbol.png)](docs/capturas/en/doble-arbol.png) |
| **Vertical pane-terminal**: a pane and its terminal side by side, with git<br>[![Vertical pane-terminal style](docs/capturas/en/panel-terminal.png)](docs/capturas/en/panel-terminal.png) | **Images**: thumbnails and a viewer on the side<br>[![Images style](docs/capturas/en/imagenes.png)](docs/capturas/en/imagenes.png) |
| **Filmstrip**: a large viewer above a strip of images<br>[![Filmstrip style](docs/capturas/en/tira.png)](docs/capturas/en/tira.png) | **A style of your own**: two panes, viewer and metadata<br>[![A custom style](docs/capturas/en/estilo-propio.png)](docs/capturas/en/estilo-propio.png) |

The four-pane layout at the top of this page is a custom style too. This is the editor, with the custom style above open:

![The style editor, with a diagram of the window](docs/capturas/en/editor-estilos.png)

## Status: version 0.1

This is a first working version. Developed for personal use.

## What else it includes

- Interface with menu, toolbar, styles row, «Location» bar, tree and a bar of its own in each pane
- Dark, light or desktop-matching theme, with its own icons and a different background for source and destination
- Compare two files, from one pane or two: whether they are identical, the differences of a text with diff, two images side by side with what differs in red, or an external comparison tool among those installed (Kompare, Meld, KDiff3, WinMerge…). Set in Preferences → Compare files
- System file manager, reversible, in Preferences → File manager: on Windows it replaces Explorer for folders on disk, with exceptions and Win+E, and on Linux it uses `xdg-mime`. It can also be undone with `directory-commander-tux-tux --restore-file-manager`
- Viewer and metadata with a button each; Preferences → General sets which one goes on top when both are shown
- Details, list, icons, tiles, thumbnails and filmstrip views
- Expandable folders within the list, natural sorting with folders first, quick filter, content filter by several types at once and flat view
- Status per pane: selection and totals, bytes and disk meter
- Copy, move and delete in the background, with progress, cancellation, collision detection and undo
- Image viewer in its own window, metadata panel, attributes and links
- Convert, rotate and resize images; split and join files; copy only what's newest
- Movable bars: drag them by their handle to another row, another edge or next to another bar, and the position is remembered. They can also be locked
- Card when resting the mouse on a file: thumbnail, dimensions, duration, codecs, tags and cover
- Double-click the tab bar to open a new tab
- Select by clicking the name; clicking elsewhere clears the selection. To go back to full-row selection: `Set FULLROWSELECT=Toggle` or the View menu
- On Windows, the path starts at «This PC», which shows drives grouped by type (SSD, HDD, other, removable, optical and network), with capacity, free space, usage bar and file system
- Folder tree with sections: Home, Favorites and the drives (the file system on Linux)
- Right-click a name: menu for that file or folder. Right-click anywhere else: menu for the open folder, without changing the selection. Also with the menu key or `Shift+F10`
- Saved layouts and tab groups, favorites bar, recent and frequent folders, navigation lock and selection checkboxes
- System trash and clipboard compatible with Explorer, Dolphin and Nautilus
- Drag files and folders between folders, panes and the tree: on the same drive it moves and to another it copies (Shift forces move and Ctrl copy). Dragging with the right button asks what to do: copy, move, create a shortcut or options from other programs, as in any file explorer. Dropping a folder on the tab bar opens it in a new tab
- Progress window when copying or moving: current item, total, times, speed graph, skip, pause and unattended mode
- Batch rename with preview: text, wildcards, regex, `[#]` numbering, case and presets
- ZIP, TAR and variants: they open with the system program or, depending on preferences, are browsed like folders (read-only). ZIP extraction and creation
- Search files by name, text, type, date, time and size, in several folders, subfolders and archives, with exclusions. It has advanced AND/OR rules, templates and history. It appears in a separate window, in one or two columns, or at the bottom of the main window, and results open in a new tab, like any other folder
- Synchronize two folders and find duplicates
- Information when hovering over files and folders
- On Windows, context menus include options from the system and other installed programs. Menus can be sorted and trimmed in the preferences
- «Open with» in a file's menu: on Windows, the system's; on Linux, the programs installed for that file type
- Folder sizes, hashes (MD5, SHA-1, SHA-256, SHA-512, BLAKE2b) and image and text viewer (images can open in the internal viewer or in the system application)
- Cropping in the image viewer: select a part with the mouse and save it as a new image
- Video and audio playback in the viewer, with position bar, volume and optional autoplay
- Video viewer in its own window: the same controls, frame by frame and saving a frame as PNG (videos can open in it or in the system application, and «Open with» always offers the other option)
- Export and import favorites and settings as JSON files
- Customizable bars: in Preferences, or by right-clicking a bar → Customize…, choose which buttons each one shows and in what order, including the styles bar, which ends with a «New style…» button
- Favorites with branches: a button in each pane to add the folder (`Ctrl+D`), choosing name and branch, or open any favorite. They are edited in Preferences → Frequent paths, where you also set recent folders, history and smart favorites, the most used folders according to the points each action gives
- Metadata panel with its own button: the data is read by [ExifTool](https://exiftool.org/), which must be installed separately (12.24 or later) and set in Preferences → General if it isn't on the PATH. Without it, only basic file data is shown
- Languages: Spanish and English, and more can be added with a file, without touching the program ([how to translate it, in Spanish](docs/TRADUCIR.md))
- Pane colors are chosen from several combinations, or by creating your own, in Preferences → General
- Configurable tab bar: new-tab button and double-click, position on any side, sizes, `»` menu or several rows, labels and ways of closing

## Run from source

Requires Python 3.12 or later.

Windows:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m directory_commander_tux_tux
```

Linux, installed for the current user with an entry in the KDE or GNOME menu:

```bash
sh packaging/linux/install.sh
```

## Installers and portable version

The packages for each version are published on the repository's releases page. If you build them yourself, they end up in `dist/`. They don't include ExifTool. PyInstaller doesn't cross-compile, so each one is built on its own system:

```bash
python -m pip install -e ".[dev]"
```

```bash
python packaging/build.py all
```

| System | Package | What it needs | How it's used |
|---|---|---|---|
| Windows | `…-windows-setup.exe` | [Inno Setup 6](https://jrsoftware.org/isinfo.php) | Installer. It doesn't ask for administrator rights and is uninstalled from Windows |
| Windows | `…-windows-portable.zip` | Nothing else | Unzip it into any folder or USB drive |
| Linux | `…_amd64.deb` | Nothing else | `sudo apt install ./file.deb` |
| Linux | `…-x86_64.AppImage` | [appimagetool](https://github.com/AppImage/appimagetool) | Make it executable and open it, without installing |
| Linux | `…-linux-portable.tar.gz` | Nothing else | Unpack it into any folder |

They can also be built one at a time with `exe`, `portable`, `installer` and `appimage`. When a `v*` tag is pushed to GitHub, the `.github/workflows/release.yml` workflow builds them all and publishes them.

### Portable version

The portable version keeps its settings in the `config` folder, next to the program, and leaves nothing in the user's settings. What turns it on is the `portable.txt` file inside it: if you delete it, the user's settings are used. It can't be the system's file manager, because it can be moved.

## Audio and video

MP4, MOV, M4A, MKV, WebM, AVI, MP3, FLAC and WAV data is read without any dependency. To see a frame of videos and read other formats, such as OGG, FFmpeg is needed: it's looked for on the `PATH` or in the folder set in Settings → Preferences. Without FFmpeg, videos show their data with an icon instead of the frame.

## Favorites and settings as JSON

Favorites are exported and imported from the Favorites menu; importing adds them without duplicates. Settings, from the Settings menu: theme, preferences, button bar, styles, layouts, tab groups, rename presets and favorites. They apply immediately.

The session, recent folders and the FFmpeg and ExifTool folders don't travel in the file, because they belong to each computer. Neither do the terminal program, the git commands or the external comparison tool: importing them would run whatever the file's creator chose.

```json
{
  "format": "directory-commander-tux-tux-favorites",
  "version": 1,
  "favorites": [{ "label": "Photos", "path": "/home/ana/Photos" }]
}
```

Bar buttons can run programs with `Run`: only import settings you trust.

## Commands

Every button, menu and shortcut runs a text command, which can also be typed on the command line
(`Ctrl+Shift+P`). The toolbar is defined in `toolbar` inside `config.json`
(`%APPDATA%\DirectoryCommanderTuxTux` or `~/.config/directory-commander-tux-tux`).

Style names in commands are the internal ones, in Spanish, as in the `STYLE=` example below.

```text
Go UP | BACK | FORWARD | ROOT | HOME | REFRESH | TABNEW | TABCLOSE | SWAP | DEST | /desktop | /downloads | "path"
Go NEWTAB[=nofocus] FROMSEL | DRIVEBUTTONS | TABGROUPSAVE | TABGROUPLIST
Copy [MOVE] [HERE] [TO="path"] [AS] [DUPLICATE] [MAKELINK=softlink|hardlink] [CREATEFOLDER="name"]
Copy UPDATEALL | UPDATEEXISTING | ADDTOARCHIVE | ARCHIVE=single | EXTRACT
Delete [NORECYCLE] [QUIET]        Undo [REDO]
CreateFolder [NAME="{date|yyyy-MM-dd} {time|HH-mm-ss}"] [ZIP]      CreateFile [NAME="name"]
FileType NEW=.txt | NEWMENU | CONTEXTMENU
Rename [ADVANCED] [PRESET=name|!list]
Select ALL | NONE | INVERT | RESELECT | SIMILAR | PATTERN=*.jpg | TOCHECKS | FROMCHECKS
Select SOURCETODEST=in|notin[,noext] [DESELECTNOMATCH]      Select DESTTOSOURCE=in|notin
Set DUAL=Off|Vertical|Horizontal|Toggle  STYLE="Vertical doble panel"  CONTENTTYPE=images  DARKMODE=Toggle
Set VIEW=Details|List|SmallIcons|LargeIcons|Tiles|Thumbnails|FilmStrip  VIEW=Cycle,List,Details
Set SORTBY=name|size|type|modified|attr|sortlist  SORTREVERSE=On|Off  FRIENDLYDATES=off|today|normal
Set FLATVIEW=Toggle  HIDDEN=Toggle  TREE=Toggle  PANETREE=Toggle  TERMINALPANE=On|Off  PANEFRAMES=Toggle  VIEWPANE=Toggle  METAPANE=Toggle  TERMINAL=Toggle  GENERALTERMINAL=Toggle  CMDLINE=Toggle  FILTER=Show
Set NAVLOCK=Toggle  CHECKBOXMODE=Toggle  EXPANDABLEFOLDERS=Toggle  GRIDLINESH=Toggle  STATUSBAR=Toggle  STATE=Source|Dest
Set DELRECYCLEBIN=Toggle  DELRECYCLECONFIRM=Toggle  UTILITY=Find|Sync|Dupe|Jobs|Log|Off[,Toggle]
Set LOCKBARS=Toggle  HOVERINFO=Toggle
Image CONVERT[=png] [ROTATE=90] [WIDTH=n] [HEIGHT=n] [PRESERVEASPECTRATIO] [HERE] [REPLACE] [PRESERVEDATE] [ADDSUFFIX]
Show [SLIDESHOW] [FULLSCREEN]     SetAttr [MODIFIED=now]         Split      Join      Find      Play
Print FOLDER [TO=clip]            Recent [CLEAR]                 Close [PROGRAM=confirm]
Prefs [LAYOUTSAVE] [LAYOUTLIST] [LAYOUTEDIT] [EXPORT] [IMPORT]
Toolbar NAME="Favorites Bar" TOGGLE | LIST | RESETDEFAULTS
Clipboard COPY | CUT | PASTE | COPYNAMES[=nopaths]
GetSizes [HASH=sha256]            Archive        Extract        Properties
Compare FILES
Favorites ADD | IMPORT | EXPORT | MENU | EDIT | SMART                     CLI DOSPROMPT
Run program --option {filepath} {allfilepath} {sourcepath} {destpath}
https://address/to/open
```

A command with an argument it doesn't understand doesn't run: `Copy SENDMAIL` is rejected instead of
behaving like a normal `Copy`.

A button can have several lines. `@keydown:none`, `@keydown:shift` and similar choose which block runs depending on the keys pressed, and `@confirm:question` asks for confirmation.

`Run` doesn't use a shell: placeholders are replaced after splitting the arguments, so
a file name is never interpreted as part of the command.

## Main shortcuts

| Shortcut | Action | Shortcut | Action |
| --- | --- | --- | --- |
| `F2` / `Ctrl+F2` | Rename / advanced | `F6` / `Shift+F6` | Copy / move to destination |
| `F3` | Viewer | `F7` | New folder |
| `F4` / `Ctrl+L` | Type path | `F5` / `Ctrl+R` | Refresh |
| `Del` / `Shift+Del` | Trash / permanently | `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `Ctrl+F` | Filter | `Ctrl+Shift+F` | Search files |
| `Ctrl+S` | Select by pattern | `Ctrl+D` | Add to favorites |
| `Ctrl+T` / `Ctrl+W` | Open / close tab | `Ctrl+1` to `Ctrl+6` | Change view |
| `Alt+←` / `Alt+→` / `Alt+↑` | Back / forward / up | `Backspace` | Up |
| `→` / `←` on a folder | Expand / collapse | `Ctrl+Alt+T` | Terminal in another window |
| `Ctrl+Shift+O` | Pane terminal | `Ctrl+Shift+G` | General terminal |

In the image viewer: `←` `→` change image, `+` `-` zoom, `0` fit, `1` actual size, `F` full screen, `S` slideshow and `Esc` close.

## License

[MIT](LICENSE). The program uses Qt for Python and, to play video and audio, FFmpeg, each with its own license (LGPL). The packages carry these and other third-party libraries: the list is in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). [ExifTool](https://exiftool.org/) isn't part of the program: it's installed separately, with its own license.

## Development

```bash
python -m pytest tests -q
```

The real trash test is skipped by default, because it leaves a file there. To run it, set `DETUX_REAL_TRASH=1`.

`directory_commander_tux_tux/core` doesn't depend on Qt. `directory_commander_tux_tux/ui` contains the interface.
