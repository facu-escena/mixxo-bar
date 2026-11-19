#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}"
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

BT="$(ls -1 "$SDK/build-tools" | sort -V | tail -n 1)"
AAPT2="$SDK/build-tools/$BT/aapt2"
D8="$SDK/build-tools/$BT/d8"
ZIPALIGN="$SDK/build-tools/$BT/zipalign"
APKSIGNER="$SDK/build-tools/$BT/apksigner"
ANDROID_JAR="$SDK/platforms/android-28/android.jar"
OUT="$ROOT/build"
KS="$ROOT/debug.keystore"

rm -rf "$OUT"
mkdir -p "$OUT/classes"

"$AAPT2" compile --dir "$ROOT/res" -o "$OUT/resources.zip"
"$AAPT2" link \
  -o "$OUT/linked.apk" \
  -I "$ANDROID_JAR" \
  --manifest "$ROOT/AndroidManifest.xml" \
  -A "$ROOT/assets" \
  --min-sdk-version 21 \
  --target-sdk-version 28 \
  "$OUT/resources.zip"

javac --release 8 -classpath "$ANDROID_JAR" -d "$OUT/classes" \
  "$ROOT"/src/com/cordiez/mixxo/bar/*.java
"$D8" --min-api 21 --lib "$ANDROID_JAR" --output "$OUT" \
  "$OUT/classes/com/cordiez/mixxo/bar/"*.class

cp "$OUT/linked.apk" "$OUT/unsigned.apk"
(cd "$OUT" && zip -q -j unsigned.apk classes.dex)
"$ZIPALIGN" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Mixxo Bar Debug, O=Cordiez, C=AR"
fi

"$APKSIGNER" sign \
  --ks "$KS" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$ROOT/mixxo-bar.apk" \
  "$OUT/aligned.apk"

echo "APK: $ROOT/mixxo-bar.apk"
