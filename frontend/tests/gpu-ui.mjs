import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({headless:true,...(process.env.STRENGTH_BROWSER_EXE?{executablePath:process.env.STRENGTH_BROWSER_EXE}:{})});
const page=await browser.newPage({viewport:{width:1600,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto(process.env.STRENGTH_URL||'http://127.0.0.1:8876');
  await page.locator('.run-item:enabled').first().click();
  await page.locator('.execution-summary').waitFor();
  assert((await page.locator('.execution-summary').innerText()).includes('CUDA + cuDSS'));
  await page.locator('.execution-summary summary').click();
  await page.getByText('已复用矩阵分解',{exact:false}).waitFor();
  await page.screenshot({path:'../docs/gpu-workbench.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('GPU result, timings and factor reuse UI checks passed.');
}finally{await browser.close();}
