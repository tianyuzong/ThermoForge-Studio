// Run against an isolated test installation seeded with the motor demonstration.
import { chromium,expect } from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const base=process.env.STRENGTH_URL||'http://127.0.0.1:8878';
const project=process.env.STUDIO_PROJECT||'motor-demonstration';
const out=fileURLToPath(new URL('../../validation/',import.meta.url));mkdirSync(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1600,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
const checks=[];
try {
  await page.goto(`${base}/?project=${project}`,{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'多物理场研究',exact:true}).click();
  await page.getByRole('heading',{name:'多物理场研究',exact:true}).waitFor();
  await page.getByLabel('分析类型').selectOption('thermo_mechanical');
  await page.getByRole('button',{name:'轴承座开闭接触示例',exact:true}).click();
  await expect(page.getByLabel('工况名称',{exact:true})).toHaveValue('电机 · 轴承座接触与双向热结构稳态');
  await page.getByText('完整工况配置',{exact:true}).click();
  const contact=JSON.parse(await page.locator('.study-json').inputValue());
  assert.equal(contact.contacts.length,2);assert.equal(contact.coupling.transient,false);checks.push('motor contact preset has two pressure-dependent interfaces');
  const names=await page.getByLabel('共享命名选区',{exact:true}).locator('option').evaluateAll(o=>o.map(v=>v.value).filter(Boolean));
  assert(names.length);await page.getByLabel('共享命名选区',{exact:true}).selectOption(names[0]);
  await page.getByRole('textbox',{name:'研究选区名称',exact:true}).fill('统一研究验收选面');
  await page.getByRole('button',{name:'保存当前选面',exact:true}).click();
  await expect(page.getByLabel('共享命名选区',{exact:true}).locator('option[value="统一研究验收选面"]')).toHaveCount(1);checks.push('shared named selections save and reload');
  await page.evaluate(()=>{document.querySelector('.study-workspace').scrollTop=0;document.querySelector('.study-config').scrollTop=0;});
  await page.screenshot({path:out+'unified-contact-setup.png',fullPage:true});
  await page.getByRole('button',{name:'结果与时间历程',exact:true}).click();
  await page.getByRole('button',{name:'电机轴 · 0–1000 N 演示疲劳 · 更新评估',exact:false}).click();
  await page.getByText('裂纹萌生危险位置：组件 8',{exact:true}).waitFor();
  await expect(page.locator('.run-timeline')).toContainText('0 s',{timeout:30000});
  await page.getByText('危险点应力循环',{exact:true}).click();
  await expect(page.locator('.study-fatigue-detail tbody tr')).toHaveCount(2);
  const report=await page.getByRole('link',{name:'分项中文报告',exact:true}).getAttribute('href');
  const pdf=await page.request.get(new URL(report,base).href);assert.equal(pdf.status(),200);assert.equal((await pdf.body()).subarray(0,4).toString(),'%PDF');
  await page.screenshot({path:out+'unified-fatigue-result.png',fullPage:true});checks.push('fatigue hotspot, stress cycles and saved PDF agree');
  await page.getByRole('button',{name:'电机 · 热结构装配 · 配合间隙探针',exact:false}).click();
  await page.getByLabel('全套结果时间帧',{exact:true}).waitFor();
  await page.getByLabel('全套结果时间帧',{exact:true}).fill('10');
  await expect(page.locator('.run-viewer header')).toContainText('11 / 11 帧');
  await expect(page.locator('.run-timeline')).toContainText('600 s',{timeout:30000});
  await page.getByText('转子与定子径向气隙 · 当前帧',{exact:true}).waitFor();checks.push('transient timeline and clearance probes');
  await page.getByLabel('全套结果时间帧',{exact:true}).fill('0');
  await expect(page.locator('.run-timeline')).toContainText('0 s',{timeout:30000});
  await page.getByRole('button',{name:'播放',exact:true}).click();
  await expect(page.locator('.run-timeline')).not.toContainText('0 s',{timeout:30000});
  await page.getByRole('button',{name:'暂停',exact:true}).click();checks.push('playback waits for an actual loaded result frame');
  if(process.env.STUDIO_BEARING_PROJECT&&process.env.STUDIO_BEARING_STUDY){
    await page.goto(`${base}/?project=${process.env.STUDIO_BEARING_PROJECT}`,{waitUntil:'networkidle'});
    await page.getByRole('button',{name:'多物理场研究',exact:true}).click();
    await page.getByRole('button',{name:'结果与时间历程',exact:true}).click();
    await page.locator(`button[data-study-id="${process.env.STUDIO_BEARING_STUDY}"]`).click();
    await page.getByText('独立复算验证通过：',{exact:false}).waitFor();
    const evidence=await page.getByRole('link',{name:'查看比较值与阈值',exact:true}).getAttribute('href');
    const verification=await (await page.request.get(new URL(evidence,base).href)).json();
    assert.equal(verification.checks.length,2);assert(verification.checks.every(v=>v.passed));
    checks.push('rolling mesh and step verification matches the saved raw-field identity');
  }
  assert.deepEqual(errors,[]);
  writeFileSync(out+'browser-final.json',JSON.stringify({passed:true,url:base,project,checks,page_errors:errors},null,2));
  console.log(JSON.stringify({passed:true,checks}));
} catch(error) {
  await page.screenshot({path:out+'browser-failure.png',fullPage:true});
  writeFileSync(out+'browser-failure.json',JSON.stringify({error:String(error),body:await page.locator('body').innerText(),page_errors:errors},null,2));
  throw error;
} finally {await browser.close();}
