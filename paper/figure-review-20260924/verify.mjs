const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1200,height:1300},deviceScaleFactor:2});
const results={};
for(const version of ['old','new']) for(const name of (version==='old'?['teaser','workflow']:['teaser','workflow','measurement','online-algorithm'])) {
  const errors=[];
  const onerror=e=>errors.push(e.message);
  page.on('pageerror',onerror);
  await page.goto(pathToFileURL(path.join(root,version,name+'.html')).href);
  await page.evaluate(()=>document.fonts.ready);
  const result=await page.evaluate(()=>{
    const svg=document.querySelector('#chart>svg'), canvas=svg.getBoundingClientRect();
    const labels=[...svg.querySelectorAll('text')];
    const text=labels.map(n=>n.textContent).join(' ');
    const outside=labels.filter(n=>{
      const b=n.getBoundingClientRect();
      return b.left<canvas.left-1 || b.right>canvas.right+1 || b.top<canvas.top-1 || b.bottom>canvas.bottom+1;
    }).map(n=>n.textContent);
    return {words:text.split(/\s+/).filter(w=>/[a-zA-Z0-9]/.test(w)).length,
      width:canvas.width,height:canvas.height,outside,labels:labels.length,equations:svg.querySelectorAll('[data-equation]').length};
  });
  result.errors=errors;
  results[version+'-'+name]=result;
  if(version==='new') {
    const svg=await page.evaluate(()=>{
      const node=document.querySelector('#chart>svg').cloneNode(true);
      node.setAttribute('xmlns','http://www.w3.org/2000/svg');
      const style=document.createElementNS('http://www.w3.org/2000/svg','style');
      style.textContent=[...document.querySelectorAll('head style')].filter(n=>n.textContent.includes('@font-face')).map(n=>n.textContent).join('\n');
      node.insertBefore(style,node.firstChild);
      return node.outerHTML;
    });
    await fs.writeFile(path.join(root,'new',name+'.svg'),svg);
    await page.locator('#chart>svg').screenshot({path:path.join(root,'new',name+'.png')});
    await page.addStyleTag({content:'#chart>svg{width:528px;height:auto;}'});
    await page.locator('#chart>svg').screenshot({path:path.join(root,'new',name+'-paper-width.png')});
  }
  page.removeListener('pageerror',onerror);
}
results.workflowWordReduction=1-results['new-workflow'].words/results['old-workflow'].words;
await fs.writeFile(path.join(root,'verification.json'),JSON.stringify(results,null,2));
console.log(JSON.stringify(results,null,2));
await browser.close();
