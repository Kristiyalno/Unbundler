# Unity Bundle Unbundler

A small GUI tool that extracts usable files out of Unity `.bundle` files (textures, sprites, audio, video, text assets, fonts, and metadata) without needing Unity itself.

Pick a single bundle file or a whole folder, and it pulls out everything it can find. Nothing gets silently dropped: any object type that isn't a recognized media format is exported as readable JSON instead of being skipped.

## Download

<!-- If you're distributing a prebuilt .exe, link it here, e.g.: -->
<!-- Grab the latest build from the [Releases](../../releases) page. No Python install required. -->

To run from source instead, see [Usage](#usage) below.

## Features

- **Images**: `Texture2D` and `Sprite` objects exported as `.png`
- **Audio**: `AudioClip` objects exported as `.wav`
- **Video**: `VideoClip` objects exported as `.mp4`, automatically muxed with their matching audio track when a bundle contains both
- **Text**: `TextAsset` objects (JSON, XML, CSV, etc) exported as `.txt`
- **Fonts**: embedded `Font` objects exported as `.ttf`/`.otf`
- **Everything else**: any other object type (SpriteAtlas data, MonoBehaviour fields, etc) is dumped as readable `.json` via Unity's type tree, with a raw binary fallback only if that fails
- **Folder mode**: recursively scans a folder for every `.bundle` file at any depth and processes them all
- **Customizable output folder names**: each bundle's output folder name is built from a format template you control (see [Naming output folders](#naming-output-folders))
- Runs extraction on a background thread with a live log, so the UI never freezes on large files

## Project structure

```
unity-bundle-unbundler/
  unbundler.py        the application
  media/
    icon.ico          window/exe icon
  README.md
  .gitignore
```

## Requirements

- Python 3
- [UnityPy](https://pypi.org/project/UnityPy/)
- [ffmpeg](https://ffmpeg.org/) on your `PATH` (only needed for video+audio muxing; everything else still works without it)
- `tkinter` (ships with standard Python on Windows/Mac; on Linux: `sudo apt install python3-tk`)

```bash
pip install UnityPy
```

## Usage

```bash
python unbundler.py
```

Click **Select File...** for a single bundle, or **Select Folder...** to process every bundle inside a directory tree, then click **Extract**.

Output is written next to whatever you selected:

- A file `foo.bundle` produces a sibling folder `unbundled_foo/`
- A folder `MyBundles/` produces a sibling folder `unbundled_MyBundles/`, mirroring the original structure, with one labeled subfolder per bundle inside

Your original files are never modified.

## Naming output folders

In folder mode, each bundle gets its own output subfolder. By default these would all look identical (just the bundle's own name), so the tool builds the folder name from a **format template** you can edit directly in the app:

```
[&] (*) %
```

Three placeholders are available:

| Placeholder | Meaning |
|---|---|
| `%` | the bundle's own name |
| `&` | the bundle's "kind", taken from its middle extension (e.g. `asset`, `spriteatlas`); empty if the bundle has no middle extension |
| `*` | a short tag describing what was found inside (`img`, `aud`, `vid+aud`, `text`, `font`, `meta`, etc) |

Type any characters you want around the placeholders, brackets, dashes, underscores, nothing is added automatically. For example:

| Template | Result |
|---|---|
| `[&] (*) %` *(default)* | `[asset] (vid+aud) doctor_cure` |
| `% & (*)` | `doctor_cure asset (vid+aud)` |
| `&-*-%` | `asset-vid+aud-doctor_cure` |
| `%` | `doctor_cure` |

If a bundle has no kind (no middle extension) or extraction fails, that placeholder is dropped along with one immediately surrounding bracket/paren pair, so `[&]` cleanly disappears instead of leaving stray empty brackets.

## How it works

Built on [UnityPy](https://github.com/K0lb3/UnityPy) for parsing Unity's serialized formats. Video and audio are read from the bundle's internal resource streams (video must be read before audio per bundle, due to a UnityPy stream-state quirk) and muxed together with `ffmpeg` when both exist for the same clip. Everything else falls through to Unity's type-tree reader, which is what makes the "nothing gets skipped" guarantee possible.

## Building a standalone .exe (Windows)

You can package this into a single `.exe` with [PyInstaller](https://pyinstaller.org/) so it runs without a Python install.

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=media\icon.ico --add-data "media;media" --collect-all UnityPy --collect-all texture2ddecoder --collect-all astc_encoder_py --collect-all etcpak --name "UnityBundleUnbundler" unbundler.py
```

The finished `.exe` is in `dist/UnityBundleUnbundler.exe`.

**Why `--collect-all` is required:** UnityPy (and its texture-decoding dependencies) use dynamic imports that PyInstaller's static analyzer can miss. Without `--collect-all`, the build succeeds but UnityPy ends up missing at runtime, showing as "UnityPy is not installed" even though it's installed fine outside the exe. `--collect-all` forces those packages to be bundled in full instead of relying on import detection.

**Why `--add-data "media;media"` is required:** the app also loads `media/icon.ico` at runtime to set the window's title bar icon (separately from the exe's own file icon set via `--icon`). `--add-data` makes sure that file actually ships inside the exe; without it, the window icon silently falls back to tkinter's default. On Mac/Linux builds, use a colon instead of a semicolon: `--add-data "media:media"`.

**Icon file format:** `media/icon.ico` needs to be a real `.ico`, not a renamed `.png`. If you only have a PNG, convert it first:

```python
from PIL import Image
Image.open("icon.png").save("media/icon.ico", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
```

**If the built exe still shows the default icon after a successful build**, this is almost always Windows Explorer's icon cache, not a broken build. Restart Explorer or clear the icon cache (`del /a /q "%localappdata%\IconCache.db"` from an elevated prompt, then restart `explorer.exe`) and check again. Right-click → Properties on the exe is a more reliable way to confirm the icon is actually embedded, since it bypasses some of Explorer's thumbnail caching.

**ffmpeg is not bundled** by PyInstaller since it's an external binary, not a Python package. Anyone running the `.exe` still needs `ffmpeg` on their system PATH for video+audio muxing; without it, video and audio are still extracted, just as separate files instead of one combined `.mp4`.

## Repo hygiene

If you build the `.exe` inside this repo's folder, make sure `build/`, `dist/`, and `*.spec` aren't committed. A `.gitignore` covering those (plus any Unity-style `StandaloneWindows64/`-type build output) is included.

## Credits

The icon (`media/icon.ico`) is adapted from the icon used by [Unity Assets Bundle Extractor Avalonia](https://forge.sp-tarkov.com/mod/204/unity-assets-bundle-extractor-avalonia). Unity Bundle Unbundler is not affiliated with or endorsed by that project.