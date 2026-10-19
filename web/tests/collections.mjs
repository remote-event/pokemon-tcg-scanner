// Exercises the real local API with two temporary collections; cleans up only those collections.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base=process.argv[2] || 'http://127.0.0.1:8765';
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
let cleanup=[];
const stamp=Date.now(), name=`Browser test ${stamp}`, other=`Move test ${stamp}`;
try {
 const page=await browser.newPage({viewport:{width:1365,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));let nav=0;page.on('framenavigated',f=>{if(f===page.mainFrame())nav++;});
 await page.goto(base);await page.getByText('Live · local network',{exact:true}).waitFor();
 await page.getByRole('button',{name:'New collection',exact:true}).click();await page.getByLabel('Collection name',{exact:true}).fill(name);await page.locator('#save').click();
 await page.locator('#page-title').getByText(name,{exact:true}).waitFor();cleanup.push(decodeURIComponent(new URL(page.url()).hash.slice(1)));
 await page.getByRole('button',{name:'Rename',exact:true}).click();await page.getByLabel('Collection name',{exact:true}).fill(name+' renamed');await page.locator('#save').click();await page.locator('#page-title').getByText(name+' renamed',{exact:true}).waitFor();
 // Mutate through a second client; the open page must update through its WebSocket.
 const second=await browser.newPage();await second.goto(base);
 await second.getByRole('button',{name:'New collection',exact:true}).click();await second.getByLabel('Collection name',{exact:true}).fill(other);await second.locator('#save').click();await second.locator('#page-title').getByText(other,{exact:true}).waitFor();cleanup.push(decodeURIComponent(new URL(second.url()).hash.slice(1)));
 await page.getByRole('button',{name:'All collections',exact:true}).click();await page.getByRole('heading',{name:other,exact:true}).waitFor();
 await page.getByRole('heading',{name:name+' renamed',exact:true}).click();
 page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'Delete collection',exact:true}).click();assert.equal(await page.locator('#page-title').textContent(),name+' renamed');
 // Mock only scan responses for rendering and action-form checks; real collection CRUD above.
 let revision=100, records=[1,2].map(i=>({id:'fixture-'+i,time:stamp+i,revision:100,collection_id:cleanup[0],card:{id:'test',name:'Same card',set:'Test set',number:'1',total:10,code:'T',image_url:''},verification:'identified',scanned_thumbnail:'/media/fixture.jpg',scanned_image:'/media/fixture-full.jpg',reference_image:'/reference/fixture.jpg',price:null}));
 const actions=[];
 await page.route('**/api/library**',async route=>{
  const req=route.request(),url=new URL(req.url());
  if(req.method()==='POST') {const v=req.postDataJSON();actions.push(v);revision++;
   if(v.action==='edit')records=records.map(r=>v.ids.includes(r.id)?{...r,revision,card:{...r.card,...v.fields},reference_image:null}:r);
   if(v.action==='delete_scans'||v.action==='move')records=records.filter(r=>!v.ids.includes(r.id));
   return route.fulfill({json:{revision}});
  }
  return route.fulfill({json:{collections:cleanup.map((id,i)=>({id,name:i?other:name+' renamed',count:records.length,created:stamp,last_scan:stamp})),active_collection:null,collection:{id:cleanup[0],name:name+' renamed',count:records.length},rows:records,total:records.length,next:null,revision}});
 });
 await page.route('**/media/**',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="630" height="880"><rect width="630" height="880" fill="teal"/></svg>'}));
 await page.route('**/reference/**',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="630" height="880"><rect width="630" height="880" fill="gray"/></svg>'}));
 await page.evaluate(()=>sync());await page.getByRole('button',{name:'Open Same card'}).first().click();assert.equal(await page.locator('dialog[open] img').count(),2);
 await page.getByRole('button',{name:'Edit details',exact:true}).click();await page.getByLabel('Card name',{exact:true}).fill('Corrected card');await page.getByLabel('Card number',{exact:true}).fill('7');await page.locator('#save').click();await page.locator('#detail-name').getByText('Corrected card',{exact:true}).waitFor();assert.equal(actions.at(-1).fields.number,'7');
 await page.locator('#close').click();await page.locator('#select-all').click();assert.equal(await page.locator('#selection-count').textContent(),'2 selected');
 page.once('dialog',d=>d.dismiss());await page.locator('#bulk-delete').click();assert.equal(records.length,2);
 await page.locator('#bulk-move').click();await page.getByLabel('Destination collection').selectOption(cleanup[1]);await page.locator('#save').click();await page.getByText('No scans yet. Select this collection and present a card to your phone.',{exact:true}).waitFor();assert.equal(actions.at(-1).ids.length,2);
 records=[{id:'fixture-3',time:stamp,revision:++revision,collection_id:cleanup[0],card:{id:'test',name:'Delete me',set:'Test',number:'3'},scanned_image:null,scanned_thumbnail:null,reference_image:null,price:null}];await page.evaluate(()=>sync());await page.getByRole('button',{name:'Open Delete me'}).click();page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Delete scan',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#detail').open);assert.equal(records.length,0);
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>performance.getEntriesByType('navigation').length),1);
 console.log('PASS: real collection creation/rename, cross-browser live updates, delete cancellation, scan comparison/edit, bulk move, confirmed deletion, mobile layout.');
} finally {
 for(const id of cleanup)await fetch(base+'/api/library/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'delete_collection',id})});
 await browser.close();
}
