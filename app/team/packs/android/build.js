// The android pack's build: checks AndroidManifest.xml, then runs gradlew assembleDebug --offline.
//
//   node build.js <the project folder (with settings.gradle.kts and gradlew.bat)>
//
// Gradle's build doesn't fail when the manifest names an Activity, Service, BroadcastReceiver or Provider class
// that doesn't exist (only lint does, and lint is slow): the app then crashes on the phone. On Day 6 the Builder
// registered a BootReceiver it never managed to write, and everything built. So the manifest is checked first:
// every component name must be a .java or .kt file under the module's source folders.
"use strict";
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const root = path.resolve(process.argv[2] || ".");
const problems = [];

function modules() {
  const settings = ["settings.gradle.kts", "settings.gradle"].map((f) => path.join(root, f)).find((f) => fs.existsSync(f));
  const text = settings ? fs.readFileSync(settings, "utf8") : "";
  const names = [...text.matchAll(/include\s*\(?\s*([^)\n]+)/g)]
    .flatMap((m) => [...m[1].matchAll(/["']:?([^"']+)["']/g)].map((x) => x[1].replace(/:/g, "/")));
  return names.length ? names : ["app"];
}

function sourceFiles(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) sourceFiles(p, out);
    else if (/\.(java|kt)$/.test(e.name)) out.push(p);
  }
  return out;
}

for (const mod of modules()) {
  const dir = path.join(root, mod);
  const manifestPath = path.join(dir, "src", "main", "AndroidManifest.xml");
  if (!fs.existsSync(manifestPath)) continue;
  const manifest = fs.readFileSync(manifestPath, "utf8");
  const gradle = ["build.gradle.kts", "build.gradle"].map((f) => path.join(dir, f)).find((f) => fs.existsSync(f));
  const namespace = ((gradle && fs.readFileSync(gradle, "utf8").match(/namespace\s*=?\s*["']([\w.]+)["']/)) || [])[1]
    || (manifest.match(/<manifest[^>]*\spackage="([\w.]+)"/) || [])[1] || "";
  const classes = new Set();
  for (const f of sourceFiles(path.join(dir, "src", "main"))) {
    const src = fs.readFileSync(f, "utf8");
    const pkg = (src.match(/^\s*package\s+([\w.]+)/m) || [])[1] || "";
    const name = path.basename(f).replace(/\.(java|kt)$/, "");
    classes.add(pkg ? pkg + "." + name : name);
  }
  const rel = path.relative(root, manifestPath).replace(/\\/g, "/");
  const lines = manifest.split("\n");
  for (const m of manifest.matchAll(/<(activity|service|receiver|provider|activity-alias)\b[^>]*?\sandroid:name="([^"]+)"/g)) {
    if (m[1] === "activity-alias") continue;
    let cls = m[2];
    if (cls.startsWith(".")) cls = namespace + cls;
    else if (!cls.includes(".")) cls = namespace + "." + cls;
    if (cls.startsWith("android.") || cls.startsWith("androidx.")) continue;
    if (!classes.has(cls)) {
      const line = manifest.slice(0, m.index).split("\n").length;
      problems.push(`${rel}:${line}: error: the manifest's <${m[1]}> names ${cls}, but there is no such class in `
        + `${mod}/src/main (the app would crash on the phone). Write the class, or fix the name.`);
    }
  }
  void lines;
}

if (problems.length) {
  problems.forEach((p) => console.log(p));
  process.exit(1);
}

const gradlew = path.join(root, "gradlew.bat");
const args = ["assembleDebug", "--offline", "--console=plain", "-q"];
const r = spawnSync("cmd.exe", ["/d", "/s", "/c", `"${gradlew}" ${args.join(" ")}`], { cwd: root, stdio: "inherit",
                                                                                         windowsVerbatimArguments: true });
process.exit(r.status === null ? 1 : r.status);
