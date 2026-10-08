const fs = require('node:fs');
const path = require('node:path');

const ROUTES = [
  ['GET', /^\/api\/v1\/(bootstrap|health|experiments|devices|environment|tasks|sessions)$/],
  ['GET', /^\/api\/v1\/tasks\/[a-zA-Z0-9_-]+(\/logs)?$/],
  ['POST', /^\/api\/v1\/(tasks|preflights)$/],
  ['POST', /^\/api\/v1\/tasks\/[a-zA-Z0-9_-]+\/(stop|force-stop)$/],
  ['GET', /^\/api\/v1\/sessions\/[a-zA-Z0-9_-]+\/(artifacts|evidence)\/.+$/],
];

function allowedRequest(url, method = 'GET') {
  if (typeof url !== 'string' || !url.startsWith('/api/v1/') || /[\\#]/.test(url) || url.length > 4096) throw Error('接口路径无效');
  const decoded = decodeURIComponent(url.split('?')[0]);
  if (decoded.split('/').some(part => part === '..' || part === '.' || part.includes(':'))) throw Error('接口路径越界');
  const parsed = new URL(url, 'http://policy.invalid');
  if (!ROUTES.some(([verb, rule]) => verb === method && rule.test(parsed.pathname))) throw Error('该桌面操作不可用');
  const queryKeys = new Set(['template_id','runtime_id','limit','after','preview','download']);
  for (const key of parsed.searchParams.keys()) if (!queryKeys.has(key)) throw Error('查询参数无效');
  return parsed.pathname + parsed.search;
}

function safeRelative(value) {
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\\:]/.test(value) || value.split('/').some(item => !item || item === '.' || item === '..')) throw Error('文件相对路径无效');
  return value;
}

function isWithin(base, file) {
  const relative = path.relative(fs.realpathSync(base), fs.realpathSync(file));
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

function trustedEvidence(request) {
  return request.method === 'GET' && request.initiatorOrigin === 'app://workbench';
}

module.exports = { allowedRequest, safeRelative, isWithin, trustedEvidence };
