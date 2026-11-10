{
  description = "phone-kvm: an ESP32-S3 board that is a USB keyboard, touchscreen and mouse with a camera, and gives an agent a phone";
  # Same nixpkgs revision as spawnkit, so common tools are shared store paths.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/55bfb4d7437bd4e09ab7cba58a43bd68eb310044";
  # ESP-IDF, its Xtensa toolchain and Espressif's QEMU fork, with the nixpkgs they are tested on.
  inputs.esp.url = "github:mirrexagon/nixpkgs-esp-dev/5287d6e1ca9e15ebd5113c41b9590c468e1e001b";
  outputs =
    { nixpkgs, esp, ... }:
    let
      espPkgs = import esp.inputs.nixpkgs {
        system = "x86_64-linux";
        overlays = [ esp.overlays.default ];
        config.permittedInsecurePackages = [ "python3.13-ecdsa-0.19.1" ];
      };
      # Espressif's QEMU with the two devices the simulated board needs and upstream lacks:
      # the USB OTG controller and a camera (sim/qemu/).
      qemu = espPkgs.qemu-esp32.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [ ./sim/qemu/machine.patch ];
        postPatch = (old.postPatch or "") + ''
          cp ${./sim/qemu/esp32s3_usb_otg.c} hw/misc/esp32s3_usb_otg.c
          cp ${./sim/qemu/esp32s3_simcam.c} hw/misc/esp32s3_simcam.c
        '';
      });
      pkgs = import nixpkgs {
        system = "x86_64-linux";
        # The Android emulator and its system image are Google binaries under the Android SDK
        # license, which Shmuel accepted for this project on 2026-11-09.
        config = {
          allowUnfree = true;
          android_sdk.accept_license = true;
        };
      };
      android = pkgs.androidenv.composeAndroidPackages {
        includeEmulator = true;
        includeSystemImages = true;
        platformVersions = [ "36" ];
        systemImageTypes = [ "google_apis" ];
        abiVersions = [ "x86_64" ];
      };
      sdk = "${android.androidsdk}/libexec/android-sdk";
    in
    {
      packages.x86_64-linux.android-sdk = android.androidsdk;
      packages.x86_64-linux.qemu = qemu;
      # Building the firmware: `nix develop .#firmware`.
      devShells.x86_64-linux.firmware = espPkgs.mkShell {
        packages = [ espPkgs.esp-idf-xtensa qemu ];
      };
      # Generating and checking the board: `nix develop .#board`.
      devShells.x86_64-linux.board = pkgs.mkShell {
        packages = [ pkgs.kicad (pkgs.python3.withPackages (p: [ p.kicad ])) ];
        # KiCad's libraries, for tools/generate.py (kicad-cli sets these only for itself).
        KICAD10_SYMBOL_DIR = "${pkgs.kicad.libraries.symbols}/share/kicad/symbols";
        KICAD10_FOOTPRINT_DIR = "${pkgs.kicad.libraries.footprints}/share/kicad/footprints";
        KICAD10_3DMODEL_DIR = "${pkgs.kicad.libraries.packages3d}/share/kicad/3dmodels";
      };
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          android.androidsdk
          qemu
          pkgs.ffmpeg
          pkgs.usbutils
          pkgs.tesseract
          (pkgs.python3.withPackages (p: [ p.numpy p.pillow p.pytest ]))
        ];
        ANDROID_HOME = sdk;
        ANDROID_SDK_ROOT = sdk;
      };
    };
}
