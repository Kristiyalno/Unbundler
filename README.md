# Unity Bundle Unbundler

A small GUI tool that extracts usable files out of Unity `.bundle` files (textures, sprites, audio, video, text assets, fonts, and metadata) without needing Unity itself.

Pick a single bundle file or a whole folder, and it pulls out everything it can find. Nothing gets silently dropped: any object type that isn't a recognized media format is exported as readable JSON instead of being skipped.

## Download

To run from source, see [Usage](#usage) below.

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
    oldicon.ico       previous icon (unused)
    339.png           original icon source (from Unity Assets Bundle Extractor Avalonia)
    339.webp          original icon source (webp)
  failedexe/
    UnityBundleUnbundler.exe  prebuilt Windows executable that does not work
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

This section only applies to **folder mode**. In single-file mode, the output folder is always named `unbundled_<bundle name>` and the template is not used.

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

If a bundle has no kind (no middle extension), `&` is dropped. If extraction did not succeed or the bundle was empty, `*` is dropped. In both cases, one immediately surrounding bracket/paren pair is also removed, so `[&]` cleanly disappears instead of leaving stray empty brackets.

## How it works

Built on [UnityPy](https://github.com/K0lb3/UnityPy) for parsing Unity's serialized formats. Video and audio are read from the bundle's internal resource streams (video must be read before audio per bundle, due to a UnityPy stream-state quirk) and muxed together with `ffmpeg` when both exist for the same clip. Everything else falls through to Unity's type-tree reader, which is what makes the "nothing gets skipped" guarantee possible.

## Building a standalone .exe (Windows)

I was not able to produce a working executable. The prebuilt `failedexe/UnityBundleUnbundler.exe` is broken: UnityPy's native decoders fail at runtime inside a PyInstaller-frozen environment due to a missing `fmod.dll` dependency that cannot be resolved without the dll being installed on the build machine. Image and audio extraction both fail as a result.

Run from source instead. See [Usage](#usage).

## Credits

The icon (`media/icon.ico`) is adapted from the icon used by [Unity Assets Bundle Extractor Avalonia](https://forge.sp-tarkov.com/mod/204/unity-assets-bundle-extractor-avalonia). Unity Bundle Unbundler is not affiliated with or endorsed by that project.