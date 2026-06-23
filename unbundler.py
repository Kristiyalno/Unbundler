"""
Unity Bundle Unbundler
-----------------------
A small GUI tool to extract usable files (textures, sprites, audio, video)
out of Unity .bundle / .asset.bundle files.

Pick a single bundle file, or a folder, and it will:
  - recursively find every *.bundle file in the folder
  - extract whatever it can (images as .png, audio as .wav, video as .mp4)
  - mux video+audio together into one .mp4 when a bundle has both
  - mirror the original folder structure into a sibling folder named
    "unbundled_<original folder name>" (the prefix is only added once,
    to the top-level output folder, not to every file/subfolder inside it)

Requirements:
    pip install UnityPy
    ffmpeg must be installed and on your PATH (for muxing video+audio).
    tkinter ships with standard Python on Windows/Mac. On Linux you may
    need: sudo apt install python3-tk

Usage:
    python unbundler.py
"""

import os
import sys
import shutil
import threading
import subprocess
import traceback
import json
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

try:
    import UnityPy
    import UnityPy.config as uconfig
except ImportError:
    UnityPy = None

FALLBACK_UNITY_VERSION = "2022.3.0f1"
BUNDLE_EXTENSIONS = (".bundle",)  # matches .bundle and .asset.bundle/.spriteatlas.bundle etc, since they all end in .bundle


def find_bundles(root_path):
    """Return list of all .bundle files under root_path (or [root_path] if it's a single file)."""
    if os.path.isfile(root_path):
        return [root_path]

    bundles = []
    for dirpath, _dirnames, filenames in os.walk(root_path):
        for fname in filenames:
            if fname.lower().endswith(BUNDLE_EXTENSIONS):
                bundles.append(os.path.join(dirpath, fname))
    return bundles


def safe_name(name, fallback, ext):
    name = (name or fallback).strip()
    if not name:
        name = fallback
    if not name.lower().endswith(ext):
        name += ext
    # strip characters that are illegal in Windows filenames
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name


def extract_bundle(bundle_path, out_dir, log_fn):
    """
    Extract one bundle file into out_dir (already created).
    Returns (status_string, detail_string).
    """
    if UnityPy is None:
        return "ERROR", "UnityPy is not installed. Run: pip install UnityPy", "error"

    try:
        uconfig.FALLBACK_UNITY_VERSION = FALLBACK_UNITY_VERSION
        env = UnityPy.load(bundle_path)
    except Exception as e:
        return "LOAD_FAILED", str(e), "error"

    try:
        files_dict = list(env.files.values())
        bundle_file = files_dict[0] if files_dict else None
    except Exception:
        bundle_file = None

    image_count = 0
    text_count = 0
    font_count = 0
    raw_count = 0
    video_clips = []   # (name, bytes)
    audio_clips = []   # (name, fname, bytes)
    other_count = 0

    objects = list(env.objects)

    # --- images first (independent of video/audio stream state) ---
    for obj in objects:
        if obj.type.name in ("Texture2D", "Sprite"):
            try:
                data = obj.read()
                img = data.image
                if img is None:
                    continue
                fname = safe_name(getattr(data, "m_Name", None), f"texture_{obj.path_id}", ".png")
                out_path = os.path.join(out_dir, fname)
                # avoid collisions
                out_path = dedupe_path(out_path)
                img.save(out_path)
                image_count += 1
            except Exception as e:
                log_fn(f"    image export error ({obj.type.name}): {e}")

    # --- text assets (json, xml, csv, plain text, etc stored as TextAsset) ---
    for obj in objects:
        if obj.type.name == "TextAsset":
            try:
                data = obj.read()
                raw = bytes(data.m_Script.encode("utf-8", "surrogateescape")) if isinstance(data.m_Script, str) else bytes(data.m_Script)
                fname = safe_name(getattr(data, "m_Name", None), f"text_{obj.path_id}", ".txt")
                out_path = dedupe_path(os.path.join(out_dir, fname))
                with open(out_path, "wb") as f:
                    f.write(raw)
                text_count += 1
            except Exception as e:
                log_fn(f"    text export error: {e}")

    # --- fonts (TTF/OTF data embedded as Font objects) ---
    for obj in objects:
        if obj.type.name == "Font":
            try:
                data = obj.read()
                raw = getattr(data, "m_FontData", None)
                if not raw:
                    continue
                raw = bytes(raw)
                # detect ttf vs otf by magic bytes
                ext = ".otf" if raw[:4] == b"OTTO" else ".ttf"
                fname = safe_name(getattr(data, "m_Name", None), f"font_{obj.path_id}", ext)
                out_path = dedupe_path(os.path.join(out_dir, fname))
                with open(out_path, "wb") as f:
                    f.write(raw)
                font_count += 1
            except Exception as e:
                log_fn(f"    font export error: {e}")

    # --- video BEFORE audio: reading AudioClip.samples mutates shared
    # stream state on the resource reader and corrupts subsequent
    # offset-based VideoClip reads from the same .resource file. ---
    if bundle_file is not None:
        for obj in objects:
            if obj.type.name == "VideoClip":
                try:
                    data = obj.read()
                    ext = data.m_ExternalResources
                    source_name = ext.m_Source.split('/')[-1]
                    res_entry = bundle_file.files.get(source_name)
                    if res_entry is None:
                        log_fn(f"    WARN: resource stream not found for video {data.m_Name}")
                        continue
                    raw = bytes(res_entry.read())
                    video_bytes = raw[ext.m_Offset: ext.m_Offset + ext.m_Size]
                    video_clips.append((data.m_Name, video_bytes))
                except Exception as e:
                    log_fn(f"    video export error: {e}")

    for obj in objects:
        if obj.type.name == "AudioClip":
            try:
                data = obj.read()
                samples = data.samples
                for fname, raw in samples.items():
                    audio_clips.append((data.m_Name, fname, raw))
            except Exception as e:
                log_fn(f"    audio export error: {e}")

    # count remaining types we recognize but don't export specially
    # (AssetBundle and MonoBehaviour have no dedicated exporter, so they
    # also fall through to the JSON typetree dump below)
    handled_types = ("Texture2D", "Sprite", "VideoClip", "AudioClip",
                      "TextAsset", "Font")
    for obj in objects:
        if obj.type.name == "AssetBundle":
            continue  # bundle manifest itself, not real content
        if obj.type.name not in handled_types:
            # try to get a fully readable JSON dump of the object's fields first;
            # this works for almost everything (SpriteAtlas, AnimationClip, Mesh
            # metadata, etc) since it walks Unity's own type tree.
            try:
                tree = obj.read_typetree()
                fname = safe_name(f"{obj.type.name}_{obj.path_id}", f"object_{obj.path_id}", ".json")
                out_path = dedupe_path(os.path.join(out_dir, fname))
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(tree, f, indent=2, default=str, ensure_ascii=False)
                raw_count += 1
                continue
            except Exception as e:
                log_fn(f"    typetree export failed for {obj.type.name}: {e}")

            # last-resort fallback: dump raw serialized bytes so nothing is lost
            try:
                raw = obj.get_raw_data()
                if raw:
                    fname = safe_name(f"{obj.type.name}_{obj.path_id}", f"raw_{obj.path_id}", ".bin")
                    out_path = dedupe_path(os.path.join(out_dir, fname))
                    with open(out_path, "wb") as f:
                        f.write(bytes(raw))
                    raw_count += 1
                else:
                    other_count += 1
            except Exception:
                other_count += 1

    # write raw video + audio to temp names, then mux pairwise if possible
    raw_dir = os.path.join(out_dir, "__raw_tmp__")
    raw_video_paths = []
    raw_audio_paths = []

    if video_clips or audio_clips:
        os.makedirs(raw_dir, exist_ok=True)
        try:
            for vname, vbytes in video_clips:
                fname = safe_name(vname, "video", ".mp4")
                p = dedupe_path(os.path.join(raw_dir, fname))
                with open(p, "wb") as f:
                    f.write(vbytes)
                raw_video_paths.append(p)

            for aname, fname, abytes in audio_clips:
                safe_fname = safe_name(fname, aname or "audio", ".wav")
                p = dedupe_path(os.path.join(raw_dir, safe_fname))
                with open(p, "wb") as f:
                    f.write(abytes)
                raw_audio_paths.append(p)

            n = max(len(raw_video_paths), len(raw_audio_paths))
            for i in range(n):
                has_v = i < len(raw_video_paths)
                has_a = i < len(raw_audio_paths)
                if has_v and has_a:
                    base = os.path.splitext(os.path.basename(raw_video_paths[i]))[0]
                    out_path = dedupe_path(os.path.join(out_dir, base + ".mp4"))
                    ok = mux(raw_video_paths[i], raw_audio_paths[i], out_path)
                    if not ok:
                        # fall back: copy video-only, copy audio-only separately
                        shutil.copy(raw_video_paths[i], dedupe_path(os.path.join(out_dir, base + ".mp4")))
                        shutil.copy(raw_audio_paths[i], dedupe_path(os.path.join(out_dir, os.path.basename(raw_audio_paths[i]))))
                elif has_v:
                    shutil.copy(raw_video_paths[i], dedupe_path(os.path.join(out_dir, os.path.basename(raw_video_paths[i]))))
                elif has_a:
                    shutil.copy(raw_audio_paths[i], dedupe_path(os.path.join(out_dir, os.path.basename(raw_audio_paths[i]))))
        finally:
            shutil.rmtree(raw_dir, ignore_errors=True)

    parts = []
    tag_bits = []
    if image_count:
        parts.append(f"{image_count} image(s)")
        tag_bits.append("img")
    if video_clips:
        tag_bits.append("vid")
    if audio_clips:
        tag_bits.append("aud")
    if video_clips or audio_clips:
        parts.append(f"{len(video_clips)} video / {len(audio_clips)} audio")
    if text_count:
        parts.append(f"{text_count} text asset(s)")
        tag_bits.append("text")
    if font_count:
        parts.append(f"{font_count} font(s)")
        tag_bits.append("font")
    if raw_count:
        parts.append(f"{raw_count} raw object(s)")
        if not tag_bits:
            tag_bits.append("meta")
    if other_count:
        parts.append(f"{other_count} object(s) skipped (no data)")

    if not parts:
        return "EMPTY", "no exportable objects found", "empty"

    content_tag = "+".join(tag_bits) if tag_bits else "misc"
    return "OK", ", ".join(parts), content_tag


def dedupe_path(path):
    """If path already exists, append _1, _2, etc before the extension."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{base}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


def mux(video_path, audio_path, out_path):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return proc.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


class UnbundlerApp:
    def __init__(self, root):
        self.root = root
        root.title("Unity Bundle Unbundler")
        root.geometry("720x520")
        root.minsize(600, 420)

        pad = {"padx": 10, "pady": 6}

        top_frame = ttk.Frame(root)
        top_frame.pack(fill="x", **pad)

        ttk.Button(top_frame, text="Select File...", command=self.pick_file).pack(side="left", padx=(0, 8))
        ttk.Button(top_frame, text="Select Folder...", command=self.pick_folder).pack(side="left")

        self.path_var = tk.StringVar(value="No file or folder selected")
        ttk.Label(root, textvariable=self.path_var, wraplength=680, foreground="#444").pack(fill="x", **pad)

        # --- naming settings ---
        settings_frame = ttk.LabelFrame(root, text="Output folder naming")
        settings_frame.pack(fill="x", **pad)

        format_row = ttk.Frame(settings_frame)
        format_row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(format_row, text="Format:").pack(side="left")
        self.name_format = tk.StringVar(value="[&] (*) %")
        self.format_entry = ttk.Entry(format_row, textvariable=self.name_format, width=40)
        self.format_entry.pack(side="left", padx=(6, 0), fill="x", expand=True)

        help_row = ttk.Frame(settings_frame)
        help_row.pack(fill="x", padx=8, pady=(0, 6))
        self.help_label = ttk.Label(
            help_row,
            text="%  = bundle name      &  = kind (asset, spriteatlas, ...)      *  = content (img, vid+aud, ...)  "
                 "Type any brackets/symbols around them yourself, e.g. [&] or (*). Missing a placeholder "
                 "(no kind, or failed extraction) removes it and one surrounding [ ] ( ) { } pair automatically.",
            foreground="#666",
            justify="left",
            font=("TkDefaultFont", 8),
            wraplength=660,
        )
        self.help_label.pack(side="left", anchor="w", fill="x", expand=True)
        # keep wraplength in sync with the actual window width so the text
        # reflows instead of clipping when the window is resized
        root.bind("<Configure>", self._on_resize_help_label)

        self.start_btn = ttk.Button(root, text="Extract", command=self.start_extraction, state="disabled")
        self.start_btn.pack(**pad)

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", **pad)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(root, textvariable=self.status_var).pack(fill="x", padx=10)

        log_frame = ttk.Frame(root)
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_box = tk.Text(log_frame, wrap="word", state="disabled", bg="#1e1e1e", fg="#d4d4d4")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scrollbar.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.selected_path = None
        self.is_folder = False

        if UnityPy is None:
            self.log("UnityPy is not installed. Run: pip install UnityPy", error=True)

        if shutil.which("ffmpeg") is None:
            self.log("WARNING: ffmpeg not found on PATH. Video+audio muxing will be skipped "
                      "(video and audio will still be extracted as separate files).", error=True)

    def _on_resize_help_label(self, event):
        # only react to the root window resizing, not every child widget's
        # own Configure events, and leave some padding on each side
        if event.widget is self.root:
            new_width = max(event.width - 40, 200)
            self.help_label.configure(wraplength=new_width)

    def build_folder_name(self, bundle_basename, kind, content_tag, status):
        """
        Build the final output folder name for one bundle from the user's
        format template (self.name_format), where:
            %  -> bundle's own name
            &  -> kind (e.g. "asset", "spriteatlas"), raw, no brackets added
            *  -> content tag (e.g. "vid+aud"), raw, no brackets added

        The template fully controls any surrounding characters, so
        "[&]" in the template produces "[asset]", "(*)" produces
        "(vid+aud)", etc. If a placeholder's underlying value is missing
        (no kind extension, or extraction wasn't OK), that placeholder is
        removed along with one immediately surrounding bracket/paren pair,
        so e.g. "[&]" with no kind just disappears instead of leaving a
        stray empty "[]". Everything else in the template (spaces,
        dashes, custom separators, etc) is kept exactly as typed.
        """
        template = self.name_format.get() or "%"

        has_kind = bool(kind)
        has_content = status == "OK"

        result = template
        result = result.replace("%", bundle_basename)
        result = result.replace("&", kind if has_kind else "\x00DROP\x00")
        result = result.replace("*", content_tag if has_content else "\x00DROP\x00")

        # remove a dropped placeholder along with one immediately
        # surrounding bracket/paren pair, if present, e.g. "[\x00DROP\x00]" -> ""
        for open_b, close_b in [("[", "]"), ("(", ")"), ("{", "}")]:
            result = result.replace(f"{open_b}\x00DROP\x00{close_b}", "")
        result = result.replace("\x00DROP\x00", "")

        # collapse any leftover doubled-up whitespace from removed pieces
        result = " ".join(result.split())

        return result.strip() or bundle_basename

    def log(self, msg, error=False):
        def _write():
            self.log_box.configure(state="normal")
            tag = "error" if error else "normal"
            self.log_box.insert("end", msg + "\n", tag)
            self.log_box.tag_config("error", foreground="#f48771")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, _write)

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="Select a .bundle file",
            filetypes=[("Unity bundle", "*.bundle"), ("All files", "*.*")],
        )
        if path:
            self.selected_path = path
            self.is_folder = False
            self.path_var.set(f"File: {path}")
            self.start_btn.configure(state="normal")

    def pick_folder(self):
        path = filedialog.askdirectory(title="Select a folder containing .bundle files")
        if path:
            self.selected_path = path
            self.is_folder = True
            self.path_var.set(f"Folder: {path}")
            self.start_btn.configure(state="normal")

    def start_extraction(self):
        if not self.selected_path:
            return
        if UnityPy is None:
            messagebox.showerror("Missing dependency", "UnityPy is not installed.\nRun: pip install UnityPy")
            return

        self.start_btn.configure(state="disabled")
        self.status_var.set("Scanning...")
        thread = threading.Thread(target=self.run_extraction, daemon=True)
        thread.start()

    def run_extraction(self):
        try:
            self._run_extraction_inner()
        except Exception:
            self.log("FATAL ERROR:\n" + traceback.format_exc(), error=True)
            self.root.after(0, lambda: self.status_var.set("Failed"))
        finally:
            self.root.after(0, lambda: self.start_btn.configure(state="normal"))

    def _run_extraction_inner(self):
        src = self.selected_path

        if self.is_folder:
            src = src.rstrip("/\\")
            parent = os.path.dirname(src)
            base = os.path.basename(src)
            out_root = dedupe_path(os.path.join(parent, f"unbundled_{base}"))
            os.makedirs(out_root, exist_ok=True)

            bundles = find_bundles(src)
            self.log(f"Found {len(bundles)} .bundle file(s) under: {src}")
            self.log(f"Output folder: {out_root}\n")

            self.root.after(0, lambda m=max(len(bundles), 1): self.progress.configure(maximum=m, value=0))

            ok_count = 0
            empty_count = 0
            fail_count = 0

            for i, bundle_path in enumerate(bundles, 1):
                rel = os.path.relpath(bundle_path, src)
                rel_dir = os.path.dirname(rel)
                full_basename = os.path.basename(bundle_path)
                bundle_basename = os.path.splitext(full_basename)[0]
                # capture a distinguishing middle extension, e.g.
                # "abyss.spriteatlas.bundle" -> kind="spriteatlas"
                # "doctor_cure.asset.bundle" -> kind="asset"
                # "plain.bundle" -> kind="" (no middle extension)
                kind = ""
                if bundle_basename.lower().endswith((".asset", ".spriteatlas")):
                    bundle_basename, kind_ext = os.path.splitext(bundle_basename)
                    kind = kind_ext.lstrip(".").lower()

                self.root.after(0, lambda v=f"[{i}/{len(bundles)}] {rel}": self.status_var.set(v))
                self.log(f"[{i}/{len(bundles)}] {rel}")

                # extract into a temp holding dir first since the final folder
                # name depends on the content tag, which we only know after
                # extraction finishes
                tmp_dir = os.path.join(out_root, rel_dir, f"__tmp_{i}__")
                os.makedirs(tmp_dir, exist_ok=True)

                status, detail, content_tag = extract_bundle(bundle_path, tmp_dir, self.log)
                self.log(f"    {status}: {detail}")

                # build final distinguishable folder name using the current
                # [kind]/(content) toggle and position settings
                final_dir_name = self.build_folder_name(bundle_basename, kind, content_tag, status)
                final_dir = dedupe_path(os.path.join(out_root, rel_dir, final_dir_name))

                if status == "OK":
                    os.makedirs(os.path.dirname(final_dir) or ".", exist_ok=True)
                    os.rename(tmp_dir, final_dir)
                    ok_count += 1
                elif status == "EMPTY":
                    empty_count += 1
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                else:
                    fail_count += 1
                    shutil.rmtree(tmp_dir, ignore_errors=True)

                self.root.after(0, lambda v=i: self.progress.configure(value=v))

            self.log(f"\nDone. {ok_count} extracted, {empty_count} empty, {fail_count} failed.")
            self.root.after(0, lambda v=f"Done: {ok_count} extracted, {empty_count} empty, {fail_count} failed": self.status_var.set(v))

        else:
            parent = os.path.dirname(src)
            base = os.path.basename(src)
            name_no_ext, ext = os.path.splitext(base)
            out_dir_name = f"unbundled_{name_no_ext}"
            out_root = dedupe_path(os.path.join(parent, out_dir_name))
            os.makedirs(out_root, exist_ok=True)

            self.root.after(0, lambda: self.progress.configure(maximum=1, value=0))
            self.root.after(0, lambda v=f"Extracting {base}...": self.status_var.set(v))
            self.log(f"Extracting: {src}")
            self.log(f"Output folder: {out_root}\n")

            status, detail, content_tag = extract_bundle(src, out_root, self.log)
            self.log(f"{status}: {detail}")
            self.root.after(0, lambda: self.progress.configure(value=1))

            if status == "EMPTY":
                try:
                    os.rmdir(out_root)
                except OSError:
                    pass

            self.root.after(0, lambda v=f"Done: {status}": self.status_var.set(v))


def load_window_icon(root, icon_path):
    """
    Set the window/title-bar icon from a multi-resolution .ico file.

    root.iconbitmap() is unreliable for this on Windows: depending on the
    Tk/Tcl build, it often only honors a single frame from the .ico (and
    that frame isn't always the largest one), which is why a properly
    multi-size icon can still show up blurry or tiny in the title bar.
    Loading each frame explicitly via Pillow and handing the full list to
    iconphoto() lets Tk pick the right resolution per context instead.
    """
    try:
        from PIL import Image, ImageTk
    except ImportError:
        # Pillow not available, fall back to the old (less reliable) method
        try:
            root.iconbitmap(icon_path)
        except tk.TclError:
            pass
        return

    try:
        ico = Image.open(icon_path)
        n_frames = getattr(ico, "n_frames", 1)
        photos = []
        for idx in range(n_frames):
            try:
                ico.seek(idx)
                frame = ico.copy()
                try:
                    frame = frame.convert("RGBA")
                except Exception:
                    pass
                photos.append(ImageTk.PhotoImage(frame))
            except Exception:
                pass

        if photos:
            # keep references alive, otherwise Tk garbage-collects them
            # and the icon silently reverts to the default
            root._icon_photo_refs = photos
            root.iconphoto(True, *photos)
    except Exception:
        # last resort fallback
        try:
            root.iconbitmap(icon_path)
        except tk.TclError:
            pass


def resource_path(relative_path):
    """
    Resolve a path that works both when running unbundler.py directly and
    when running from a PyInstaller-built exe. PyInstaller extracts bundled
    data files to a temporary folder at runtime and exposes it as
    sys._MEIPASS; outside of that, just resolve relative to this script.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def main():
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    icon_path = resource_path(os.path.join("media", "icon.ico"))
    if os.path.exists(icon_path):
        load_window_icon(root, icon_path)

    app = UnbundlerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()