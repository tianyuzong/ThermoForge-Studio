// Optional local browser smoke test. Start the service before running npm test.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({headless:true,...(process.env.STRENGTH_BROWSER_EXE?{executablePath:process.env.STRENGTH_BROWSER_EXE}:process.platform==='win32'?{channel:'msedge'}:{})});
const page=await browser.newPage({viewport:{width:1440,height:960}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
  await page.goto(process.env.STRENGTH_URL||'http://127.0.0.1:8877');
  await page.getByRole('heading',{name:'仿真 Agent',exact:true}).waitFor();
  await page.getByRole('button',{name:'计算资源',exact:true}).click();
  await page.getByRole('heading',{name:'计算资源',exact:true}).waitFor();
  assert(await page.getByText('CPU 计算配置',{exact:true}).isVisible());
  assert(await page.getByText('原生 CUDA + cuDSS · FP64',{exact:true}).isVisible());
  const gpu=page.locator('label').filter({hasText:'原生 CUDA + cuDSS · FP64'}).getByRole('radio');
  const health=await (await page.request.get(new URL('/api/v1/health',page.url()).href)).json();
  assert.equal(await gpu.isEnabled(),health.execution.backends.find(b=>b.id==='cudss_cuda').available);
  assert(await page.locator('label').filter({hasText:'CalculiX 2.23 · CPU'}).getByRole('radio').isEnabled());
  await page.screenshot({path:'../.runtime/cpu-resources.png',fullPage:true});
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  assert(await page.getByRole('textbox',{name:'仿真指令',exact:true}).isVisible());
  assert.deepEqual(errors,[]);
  console.log('Workbench, compute resources and Agent composer smoke checks passed.');
} finally {await browser.close();}
