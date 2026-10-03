import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
const base=process.env.STRENGTH_URL||'http://127.0.0.1:8877';
const records=await (await fetch(base+'/api/v1/analysis-runs')).json();
const id=process.env.STUDIO_RUN||records.find(r=>r.nodes?.slice(0,4).every(n=>n.status==='completed'))?.id;
if(!id)throw Error('先运行全套分析，或通过 STUDIO_RUN 指定可用的验收任务。此测试不会提交耗时计算。');
await fs.mkdir('../validation/run-ui',{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge',args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1580,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
try{
 await page.goto(base+'/?run='+id);await expect(page.getByRole('heading',{name:'指令仿真',exact:true})).toBeVisible();
 await expect(page.locator('.run-node')).toHaveCount(6);await expect(page.locator('.run-canvas canvas')).toBeVisible({timeout:120000});
 await expect(page.locator('.run-viewer header')).toContainText('非瞬态过程');
 await expect(page.locator('.run-timeline')).toHaveCount(0);
 await page.screenshot({path:'../validation/run-ui/dashboard.png',fullPage:true});
 await page.locator('.run-node').nth(3).click();await expect(page.getByLabel('全套结果时间帧')).toBeVisible({timeout:120000});
 await page.getByLabel('全套结果时间帧').fill('5');await expect(page.locator('.run-timeline')).toContainText('300',{timeout:120000});
 await page.reload();await expect(page.locator('.run-node')).toHaveCount(6);
 await page.goto(base+'/?run='+id+'&node=3&capture=1');await page.waitForFunction('typeof window.studioCapture==="function"');
 await page.evaluate(()=>window.studioCapture({frame:5,field:'temperature',angle:null}));
 await expect(page.locator('.run-viewer header')).toContainText('300');await page.screenshot({path:'../validation/run-ui/thermal-frame.png'});
 if(errors.length)throw Error(errors.join('\n'));console.log('PASS: six nodes, saved thermal frame, reload, capture hook; no JavaScript errors');
}finally{await browser.close()}
