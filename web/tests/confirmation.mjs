// Browser-only pending-scan fixtures: no production collection writes.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
try {
  const page=await browser.newPage({viewport:{width:390,height:844}}), errors=[], actions=[];
  page.on('pageerror',e=>errors.push(e.message));
  let rows=[{request_id:'pending-test',matches:[
    {ocr_support:true,card:{id:'a',name:'Pikachu',set:'First set',number:'001',total:100}},
    {ocr_support:true,card:{id:'b',name:'Pikachu',set:'Second set',number:'001',total:200}}]}];
  await page.route('**/api/library/pending',route=>route.fulfill({json:{rows}}));
  await page.route('**/api/library/pending/*/image',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="63" height="88"><rect width="63" height="88" fill="teal"/></svg>'}));
  await page.route('**/api/library/confirm',route=>{actions.push(route.request().postDataJSON());rows=[];return route.fulfill({json:{event:'recognized'}});});
  await page.goto(process.argv[2] || 'http://127.0.0.1:8765');
  await page.getByText('Confirm this card',{exact:true}).waitFor();
  assert.equal(actions.length,0);
  await page.getByRole('button',{name:/Pikachu.*Second set/}).click();
  await page.waitForFunction(()=>document.querySelector('#pending-scans').hidden);
  assert.deepEqual(actions,[{request_id:'pending-test',card_id:'b'}]);
  rows=[{request_id:'skip-test',matches:[]}];await page.evaluate(()=>pendingScans());
  await page.getByRole('button',{name:'Skip this scan'}).click();
  await page.waitForFunction(()=>document.querySelector('#pending-scans').hidden);
  assert.equal(actions[1].card_id,null);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log('PASS: no automatic confirmation; explicit set choice; skip; narrow layout; no browser errors.');
} finally { await browser.close(); }
