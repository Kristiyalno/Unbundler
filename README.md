# Unity Bundle Unbundler

A small GUI tool that extracts usable files out of Unity `.bundle` files (textures, sprites, audio, video, text assets, fonts, and metadata) without needing Unity itself.

Pick a single bundle file or a whole folder, and it pulls out everything it can find. No asset gets silently dropped: anything that isn't a recognized media type gets exported as readable JSON instead of being skipped.

## Features

- **Images**: `Texture2D` and `Sprite` objects exported as `.png`
- **Audio**: `AudioClip` objects exported as `.wav`
- **Video**: `VideoClip` objects exported as `.mp4`, automatically muxed with their matching audio track when a bundle contains both
- **Text**: `TextAsset` objects (JSON, XML, CSV, etc) exported as `.txt`
- **Fonts**: embedded `Font` objects exported as `.ttf`/`.otf`
- **Everything else**: any other object type (SpriteAtlas data, MonoBehaviour fields, etc) is dumped as readable `.json` via Unity's type tree, with a raw binary fallback only if that fails
- **Folder mode**: recursively scans a folder for every `.bundle` file at any depth and processes them all
- **Labeled output folders**: each bundle's output folder is prefixed with `[kind] (content)` tags (e.g. `[spriteatlas] (img) abyss`) so folders sort and group sensibly instead of looking identical
- Runs extraction on a background thread with a live log, so the UI never freezes on large files

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

## How it works

Built on [UnityPy](https://github.com/K0lb3/UnityPy) for parsing Unity's serialized formats. Video and audio are read from the bundle's internal resource streams (video must be read before audio per bundle, due to a UnityPy stream-state quirk) and muxed together with `ffmpeg` when both exist for the same clip. Everything else falls through to Unity's type-tree reader, which is what makes the "nothing gets skipped" guarantee possible.

## Building a standalone .exe (Windows)

You can package this into a single `.exe` with [PyInstaller](https://pyinstaller.org/) so it runs without a Python install.

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=icon.ico --collect-all UnityPy --collect-all texture2ddecoder --collect-all astc_encoder_py --collect-all etcpak --name "UnityBundleUnbundler" unbundler.py
```

The finished `.exe` is in `dist/UnityBundleUnbundler.exe`.

**Why `--collect-all` is required:** UnityPy (and its texture-decoding dependencies) use dynamic imports that PyInstaller's static analyzer can miss. Without `--collect-all`, the build succeeds but UnityPy ends up missing at runtime, showing as "UnityPy is not installed" even though it's installed fine outside the exe. `--collect-all` forces those packages to be bundled in full instead of relying on import detection.

**Icon:** needs to be a `.ico` file, not `.png`. If you only have a PNG, convert it first:

```python
from PIL import Image
Image.open("icon.png").save("icon.ico", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
```

Note `--icon` only sets the icon shown on the `.exe` file itself in Explorer; it doesn't change the window's title bar icon while the app is running.

**ffmpeg is not bundled** by PyInstaller since it's an external binary, not a Python package. Anyone running the `.exe` still needs `ffmpeg` on their system PATH for video+audio muxing; without it, video and audio are still extracted, just as separate files instead of one combined `.mp4`.