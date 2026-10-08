const parameters=new URLSearchParams(location.search);
const file=parameters.get('file')||'';
const url=`app://evidence/api/v1/sessions/${encodeURIComponent(parameters.get('session'))}/${parameters.get('category')}/${file.split('/').map(encodeURIComponent).join('/')}`;
const root=document.getElementById('preview');
document.title=file;
if(/\.(png|jpe?g)$/i.test(file)){const image=document.createElement('img');image.src=url;image.alt=file;root.replaceChildren(image);}
else if(/\.pdf$/i.test(file)){const frame=document.createElement('iframe');frame.src=url;frame.title=file;root.replaceChildren(frame);}
else fetch(url+'?preview=true').then(response=>{if(!response.ok)throw Error('无法读取证据');return response.json();}).then(data=>{const text=document.createElement('pre');text.textContent=data.text;root.replaceChildren(text);if(data.truncated){const note=document.createElement('p');note.textContent='仅显示前2MiB，完整内容请打开原文件。';root.prepend(note);}}).catch(error=>root.textContent=error.message);
