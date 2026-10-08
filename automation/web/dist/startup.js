const status=document.querySelector('#status'),error=document.querySelector('#error'),retry=document.querySelector('#retry');
async function refresh(){
  const value=await window.iotDesktop.status();
  status.textContent=value.state==='failed'?'本机服务暂不可用':value.state==='draining'?(value.active_owned?`正在安全清理，等待 ${value.active_owned} 个所属进程退出…`:'正在安全清理并保存日志…'):value.state==='recovery'?'正在核查上次运行的所属进程…':'正在准备本机运行环境…';
  error.textContent=value.error || '';
  retry.hidden=value.state!=='failed';
  document.querySelector('#help').textContent=value.state==='failed'?'请检查运行环境完整性。原有实验记录保留在工作区中。':value.state==='draining'?'实验停止和日志保存完成后，窗口会自动关闭。':value.state==='recovery'?'所属进程清理完成前不能启动新实验。':'首次打开需要初始化私有自动化环境。';
}
retry.addEventListener('click',async()=>{retry.disabled=true;try{await window.iotDesktop.retryBackend();}catch(reason){error.textContent=reason.message;}finally{retry.disabled=false;}});
refresh().catch(reason=>{error.textContent=reason.message;});
setInterval(()=>refresh().catch(()=>{}),500);
