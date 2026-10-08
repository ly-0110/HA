const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
function atomicJSON(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive:true});
  const temporary = file + '.' + crypto.randomUUID() + '.tmp';
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, 'wx');
    fs.writeFileSync(descriptor, JSON.stringify(value, null, 2));
    fs.fsyncSync(descriptor); fs.closeSync(descriptor); descriptor=undefined;
    fs.renameSync(temporary, file);
  } finally {
    if (descriptor!==undefined) fs.closeSync(descriptor);
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}
module.exports = {atomicJSON};
