// Stock Lucide Workflow glyph; only the application badge palette is customized.
const fs=require('node:fs');
const path=require('node:path');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {Workflow}=require('lucide-react');
const sharp=require('sharp');
const desktop=path.resolve(__dirname,'..');
const assets=path.join(desktop,'assets');
const web=path.resolve(desktop,'../web/public');
fs.mkdirSync(assets,{recursive:true});
const glyph=renderToStaticMarkup(React.createElement(Workflow,{size:24,color:'#ffffff',strokeWidth:1.8}));
const body=glyph.slice(glyph.indexOf('>')+1,glyph.lastIndexOf('</svg>'));
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><defs><linearGradient id="badge" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4f46e5"/><stop offset="1" stop-color="#2563eb"/></linearGradient></defs><rect x="16" y="16" width="480" height="480" rx="112" fill="url(#badge)"/><g transform="translate(88 88) scale(14)" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${body}</g></svg>`;
(async()=>{
  fs.writeFileSync(path.join(assets,'workbench.svg'),svg+'\n');
  fs.writeFileSync(path.join(web,'workbench.svg'),svg+'\n');
  await sharp(Buffer.from(svg)).png().toFile(path.join(assets,'workbench.png'));
  const sizes=[16,24,32,48,64,128,256];
  const images=await Promise.all(sizes.map(size=>sharp(Buffer.from(svg)).resize(size,size).png().toBuffer()));
  const header=Buffer.alloc(6+16*sizes.length);header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);
  let offset=header.length;
  images.forEach((buffer,index)=>{const p=6+16*index;header[p]=sizes[index]===256?0:sizes[index];header[p+1]=header[p];header.writeUInt16LE(1,p+4);header.writeUInt16LE(32,p+6);header.writeUInt32LE(buffer.length,p+8);header.writeUInt32LE(offset,p+12);offset+=buffer.length;});
  fs.writeFileSync(path.join(assets,'workbench.ico'),Buffer.concat([header,...images]));
  fs.copyFileSync(path.join(assets,'workbench.ico'),path.join(web,'favicon.ico'));
  fs.copyFileSync(path.resolve(desktop,'../node_modules/lucide-react/LICENSE'),path.join(assets,'lucide-LICENSE'));
  console.log('Generated SVG, PNG and seven-resolution ICO from Lucide Workflow.');
})();
