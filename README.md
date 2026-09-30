# CrossOver OMM Patcher

Run Logitech **Onboard Memory Manager (OMM)** with a compatible mouse on macOS through CrossOver. This script changes CrossOver's USB device filter so OMM can see the mouse. It patches your **installed CrossOver app**, so the change applies to all its bottles. It saves the original file for restoration and refuses to patch an unrecognized code layout. These files contain no CrossOver or Logitech software.

**Hardware tested:** Apple Silicon Mac, CrossOver 26.3, OMM 2.6.1749, and Logitech PRO X2 SUPERSTRIKE, with a direct cable and the receiver. The working `OMM` bottle was Windows 7, 64-bit, with Microsoft .NET Framework 4.8. Other CrossOver versions may have a matching filter, but they have **not** been tested with hardware. This project is not maintained for future releases.

## Setup

1. Install [CrossOver for Mac](https://www.codeweavers.com/crossover/download) and make a fresh 64-bit bottle named `OMM`. Our tested bottle used Windows 7.
2. In that bottle, install **Microsoft .NET Framework 4.8** through CrossOver's *Install Software*. This is the version in our working setup; OMM's error requested the .NET 4 runtime.
3. Download the **Windows** release of [Logitech OMM](https://support.logi.com/hc/en-us/articles/38103470390935-Onboard-Memory-Manager). We tested version **2.6.1749**. Its `.exe` is a standalone app, so put it in a stable location inside the bottle. In CrossOver, select the bottle, use *Run Command* → *Browse* to select the `.exe`, then *Save Command as Launcher*. If entering a path manually, quote the full path when it contains spaces.
4. Quit CrossOver. Put `patch_crossover_omm.py` somewhere convenient, open Terminal in that folder, and run:

   ```sh
   python3 patch_crossover_omm.py apply
   ```

   If macOS denies access to `/Applications/CrossOver.app`, run `sudo python3 patch_crossover_omm.py apply` instead. If CrossOver is installed elsewhere, add `--app "/path/to/CrossOver.app"`. If your bottle has another name, add `--bottle "/path/to/your/bottle"`. The USB patch works even when no OMM shortcut is found; the shortcut update is only for an existing OMM **2.6.1749** launcher. Rerun `apply` after making that launcher if needed.
5. Reopen CrossOver, connect a compatible Logitech mouse by cable or receiver, and launch OMM from its bottle.

## Check or undo

```sh
python3 patch_crossover_omm.py status
python3 patch_crossover_omm.py restore
```

The original USB file is saved in `~/Library/Application Support/CrossOver OMM Patcher`; keep that backup if you want to restore the original. Use the same `--app` or `--bottle` arguments for `status` and `restore` if you used them for `apply`. A CrossOver update may replace the patched file; check `status` afterward and rerun `apply` only if the new version has a matching layout. OMM supports [compatible Logitech mice](https://support.logi.com/hc/en-001/articles/360057433714-How-to-enable-On-Board-Memory-Mode-to-use-Onboard-Memory-Manager), not every Logitech device.
