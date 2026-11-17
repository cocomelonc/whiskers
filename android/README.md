# whiskers · Android mini TI chat

Independent application ID: `com.cocomelonc.whiskers`. English chat, original TI analyst kitten,
real public Malpedia evidence, source export, original YARA export, local GGUF inference.
The APK uses no network permission and does not require the Whiskers web server.

```bash
./gradlew --offline assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Build from this directory after obtaining the independent Whiskers JNI library and matching GGUF.
Prepare source/model assets from the repository root with:

```bash
python3 android/prepare_assets.py --model artifacts/whiskers-ti-0.6b-Q4_K_M.gguf --training-summary slm/evidence/training-summary.json
```

The native source includes `native/build_arm64.sh` for the pinned llama.cpp/NDK build.
The current native target is ARMv8.2 with dot-product support; the test phone is a moto g54 5G.
The bundled model must match `app/src/main/assets/model-info.json`; the app verifies its SHA-256
before loading it. The model file is copied once into
this app's private storage. Allow roughly 1 GB free storage for APK plus model installation.

The chat selects evidence for the selected family and displays original source text. It is a
focused TI assistant, not an unrestricted general chatbot or an endpoint malware scanner.
Each turn asks about the selected evidence; ambiguous follow-up questions may need a full question.
Unknowns and generation errors remain visible. Sources carry their record date and original URL.

Model, data, training, evaluation and export details: [../slm/README.md](../slm/README.md).
Mascot: [../artwork/README.md](../artwork/README.md). Fonts and source notices are in the APK assets.
