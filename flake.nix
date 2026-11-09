{
  description = "phone-kvm: a USB keyboard, touchscreen and mouse plus HDMI capture that gives an agent a phone";
  # Same nixpkgs revision as spawnkit, so common tools are shared store paths.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/55bfb4d7437bd4e09ab7cba58a43bd68eb310044";
  outputs =
    { nixpkgs, ... }:
    let
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
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          android.androidsdk
          pkgs.ffmpeg
          pkgs.v4l-utils
          pkgs.usbutils
          (pkgs.python3.withPackages (p: [ p.pillow p.pytest ]))
        ];
        ANDROID_HOME = sdk;
        ANDROID_SDK_ROOT = sdk;
      };
    };
}
