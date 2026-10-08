const fs = require('node:fs');
const path = require('node:path');

// npm ci --ignore-scripts is intentional. Prepare only the pinned maker's
// bundled host executables instead of running every dependency install hook.
if (process.platform === 'win32') {
  if (!['x64', 'arm64'].includes(process.arch)) throw Error('Unsupported build host architecture');
  const vendor = path.join(path.dirname(require.resolve('electron-winstaller/package.json')), 'vendor');
  for (const extension of ['exe', 'dll']) {
    fs.copyFileSync(path.join(vendor, `7z-${process.arch}.${extension}`), path.join(vendor, `7z.${extension}`));
  }
}
