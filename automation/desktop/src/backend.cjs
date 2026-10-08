const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const { allowedRequest } = require('./policy.cjs');

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const hasExited = child => !child || child.exitCode != null || child.signalCode != null;
const waitForExit = child => new Promise(resolve => hasExited(child) ? resolve() : child.once('exit', resolve));
function validateReady(message, instance, pid) {
  if(message.instance_id!==instance || message.protocol_version!==1 || message.pid!==pid || !Number.isInteger(message.port) || message.port<1 || message.port>65535) throw Error('后台就绪身份不匹配');
}
function validateHealth(ok, health, instance) {
  if(!ok || health.instance_id!==instance || health.protocol_version!==1) throw Error('后台认证失败');
}
function handshakeDeadline(child, finish) {
  return setTimeout(()=>{child.stdin.end();finish(Error('后台在30秒内未完成认证握手'));},30000);
}

class Backend {
  constructor(resources, paths) {
    this.resources = resources;
    this.paths = paths;
    this.instance = crypto.randomUUID();
    this.token = crypto.randomBytes(32).toString('base64url');
    this.manifest = JSON.parse(fs.readFileSync(path.join(resources, 'runtime-manifest.json'), 'utf8'));
    if (this.manifest.schema !== 1 || this.manifest.protocol_version !== 1) throw Error('运行时清单不兼容');
    if(this.manifest.target!==(process.platform==='win32'?'windows-x64':'linux-x64'))throw Error('运行时目标平台不兼容');
    this.python = fs.realpathSync(path.resolve(resources, this.manifest.executables.python));
    this.pythonCommand = process.platform==='win32' ? path.toNamespacedPath(this.python) : this.python;
    if (!this.python.startsWith(fs.realpathSync(resources) + path.sep)) throw Error('解释器路径越界');
    const expected = this.manifest.files[this.manifest.executables.python];
    if (crypto.createHash('sha256').update(fs.readFileSync(this.python)).digest('hex') !== expected) throw Error('私有Python校验失败');
    const prefix=`runtime/${this.manifest.target}/python/`;
    for(const [relative,checksum] of Object.entries(this.manifest.files)){
      if(!relative.startsWith(prefix))continue;
      const file=fs.realpathSync(path.resolve(resources,relative));
      if(!file.startsWith(fs.realpathSync(resources)+path.sep) || crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')!==checksum)throw Error('内置Python组件校验失败：'+relative);
    }
    this.stderr = '';
    this.state = 'starting';
  }
  environment() {
    const environment = { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' };
    for (const key of ['PYTHONPATH','PYTHONHOME','SSLKEYLOGFILE','IOT_EXP_CONTEXT','IOT_EXP_INSTANCE_ID']) delete environment[key];
    return environment;
  }
  async prepare() {
    await new Promise((resolve, reject) => {
      const child = spawn(this.pythonCommand, ['-I','-B','-X','utf8','-m','iot_exp.runtime_bundle','--resources',this.resources,'--app-state',this.paths.app_state], { env:this.environment(), windowsHide:true, stdio:['ignore','ignore','pipe'] });
      this.preparingChild=child;
      child.stderr.on('data', data => { this.stderr = (this.stderr + data).slice(-12000); });
      child.on('error', reject);
      child.on('exit', code => code === 0 ? resolve() : reject(Error('私有运行环境准备失败：' + this.stderr)));
    });
  }
  async start() {
    await this.prepare();
    if(this.stopRequested){this.state='stopped';throw Error('初始化已取消');}
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = error => { if(settled) return; settled=true; clearTimeout(timer); error ? reject(error) : resolve(); };
      this.child = spawn(this.pythonCommand, ['-I','-B','-X','utf8','-m','iot_exp.desktop_service'], { env:this.environment(), windowsHide:true, stdio:['pipe','pipe','pipe'] });
      const timer = handshakeDeadline(this.child, finish);
      this.child.on('error', finish);
      this.child.stderr.on('data', data => { this.stderr = (this.stderr + data).slice(-12000); });
      this.child.on('exit', code => { this.state=this.stopRequested && code===0?'stopped':'failed'; finish(Error('后台退出：' + code + '\n' + this.stderr)); });
      const lines = readline.createInterface({input:this.child.stdout});
      lines.on('line', async line => {
        let message; try { message=JSON.parse(line); } catch { return; }
        if(message.kind !== 'ready') return;
        try {
          if(this.stopRequested)throw Error('后台初始化已取消');
          validateReady(message,this.instance,this.child.pid);
          this.base = `http://127.0.0.1:${message.port}`;
          this.readyMessage=message;
          const response = await this.raw('/api/v1/health');
          const health = await response.json();
          validateHealth(response.ok,health,this.instance);
          this.state=health.state;
          finish();
        } catch(error) { this.child.stdin.end(); finish(error); }
      });
      this.child.stdin.write(JSON.stringify({ token:this.token, instance_id:this.instance, parent_pid:process.pid,
        paths:{...this.paths,resources:this.resources,runtime:path.join(this.resources,'runtime',this.manifest.target)} }) + '\n');
    });
  }
  raw(url, options={}) {
    if(!this.base) throw Error('后台尚未就绪');
    return fetch(this.base+url, {...options, headers:{'x-control-token':this.token,'origin':'app://workbench', ...(options.body ? {'content-type':'application/json'} : {})}, signal:AbortSignal.timeout(20000)});
  }
  async request(input) {
    if(!input || typeof input !== 'object') throw Error('桌面请求无效');
    const method=input.method || 'GET';
    const route=allowedRequest(input.url,method);
    if(input.body != null && (typeof input.body !== 'string' || Buffer.byteLength(input.body)>512*1024)) throw Error('请求内容过大或无效');
    const response=await this.raw(route,{method,body:input.body});
    const text=await response.text();
    if(!response.ok) { let reason=text; try { reason=JSON.parse(text).detail || text; } catch {} throw Error(typeof reason==='string' ? reason : JSON.stringify(reason)); }
    const result=JSON.parse(text);
    if(route==='/api/v1/health' && this.state!=='draining')this.state=result.state;
    return result;
  }
  async stop() {
    if(this.stopPromise)return this.stopPromise;
    this.stopPromise=this.stopOwned().catch(error=>{this.stopPromise=null;this.state='failed';throw error;});
    return this.stopPromise;
  }
  async stopOwned() {
    this.stopRequested=true;
    this.state='draining';
    if(!hasExited(this.preparingChild))await waitForExit(this.preparingChild);
    if(hasExited(this.child)){this.state=this.child && this.child.exitCode!==0?'failed':'stopped';return;}
    if(!this.base){this.child.stdin.end();await waitForExit(this.child);this.state='stopped';return;}
    await this.raw('/api/v1/desktop/drain',{method:'POST'});
    while(!hasExited(this.child)) {
      const response=await this.raw('/api/v1/health');
      const health=await response.json();
      validateHealth(response.ok,health,this.instance);
      if(!health.active_owned && health.state!=='recovery') break;
      await wait(200);
    }
    if(hasExited(this.child) && this.child.exitCode!==0)throw Error('后台异常退出，实验状态需要恢复核查');
    this.child.stdin.end();
    await waitForExit(this.child);
    if(this.child.exitCode!==0)throw Error('后台退出未完成，实验状态需要恢复核查');
    this.state='stopped';
  }
}
module.exports={Backend,wait,validateReady,validateHealth,handshakeDeadline,hasExited};
