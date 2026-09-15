/* Import the native scene into excalidraw.com and inspect/export through its UI. */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const ROOT = __dirname;
(async () => {
  const browser = await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext({viewport:{width:2400,height:1880},locale:'en-US'});
  const page = await context.newPage();
  await page.addInitScript(() => { window.showOpenFilePicker=undefined; window.showSaveFilePicker=undefined; });
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  await page.goto('https://excalidraw.com/',{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:/^(열기|Open)/}).first().waitFor({timeout:45000});
  const chooser=page.waitForEvent('filechooser');
  await page.getByRole('button',{name:/^(열기|Open)/}).first().click();
  await (await chooser).setFiles(path.join(ROOT,'week-03-architecture.excalidraw'));
  await page.waitForFunction(()=>!document.body.innerText.includes('Your drawings are saved in your browser'));
  await page.keyboard.press('Escape');
  await page.keyboard.press('Shift+Digit1');
  await page.waitForTimeout(1200);
  await page.screenshot({path:path.join(ROOT,'architecture-preview.png')});
  console.log(JSON.stringify({errors,body:(await page.locator('body').innerText()).slice(-2000),storageKeys:await page.evaluate(()=>Object.keys(localStorage))}));
  console.log(JSON.stringify(await page.locator('button').evaluateAll(bs=>bs.map(b=>({text:b.innerText,label:b.getAttribute('aria-label'),title:b.getAttribute('title'),'data-testid':b.getAttribute('data-testid')})))));
  await browser.close();
})().catch(e=>{console.error(e.stack);process.exit(1)});
