import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
const projectId=process.env.STRENGTH_PROJECT_ID;
if(!projectId)throw new Error('Set STRENGTH_PROJECT_ID to a demo project with completed results');
const output=process.env.STRENGTH_VISUAL_CHECK_DIR||'../screenshots';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.STRENGTH_BROWSER_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],mutations=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method()))mutations.push(r.url());});
try {
  await page.goto((process.env.STRENGTH_URL||'http://127.0.0.1:8876')+'/?project='+encodeURIComponent(projectId));
  const model=page.getByRole('button',{name:'模型',exact:true}),results=page.getByRole('button',{name:'结果',exact:true});
  await expect(results).toBeEnabled({timeout:60000});
  await expect(model).toHaveClass('active');
  await expect(page.getByRole('heading',{name:'工件与边界条件',exact:true})).toBeVisible();
  await expect(page.locator('.legend')).toHaveCount(0);
  await page.screenshot({path:output+'/initial-model.png',fullPage:true});
  await results.click();
  await expect(page.getByRole('button',{name:'播放变形',exact:true})).toBeVisible();
  await page.reload();
  await expect(results).toBeEnabled({timeout:60000});
  await expect(model).toHaveClass('active');
  // Switch to another existing project and back; never create or change a job.
  const projects=await (await page.request.get(new URL('/api/v1/projects',page.url()).href)).json();
  const jobs=await (await page.request.get(new URL('/api/v1/jobs',page.url()).href)).json();
  const other=projects.find(p=>p.id!==projectId&&jobs.some(j=>j.project_id===p.id&&j.model_id===p.model_id&&j.status==='completed'&&j.summary?.mesh?.elements<10000));
  async function switchProject(id){
    const project=projects.find(p=>p.id===id);
    const latest=jobs.find(j=>j.project_id===id&&j.model_id===project.model_id&&j.status==='completed');
    const loaded=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v1/jobs/'+latest.id+'/display');
    await page.getByRole('combobox',{name:'选择项目',exact:true}).selectOption(id);
    await (await loaded).finished();
    await expect(results).toBeEnabled({timeout:60000});
    await expect(model).toHaveClass('active');
  }
  if(other){
    await results.click();
    await switchProject(other.id);
    await results.click();
    await switchProject(projectId);
  }
  await page.locator('.run-item:enabled').first().click();
  await expect(page.getByRole('button',{name:'播放变形',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'仿真工作台',exact:true}).click();
  await expect(model).toHaveClass('active');
  await results.click();
  await page.getByRole('button',{name:'播放变形',exact:true}).waitFor({timeout:60000});
  assert.equal(await page.getByText('浏览器无法创建 WebGL 视图，请启用硬件加速。',{exact:true}).count(),0);
  await page.getByRole('button',{name:'放大视图',exact:true}).click();
  await page.getByRole('button',{name:'自动放大',exact:true}).click();
  await page.getByRole('button',{name:'播放变形',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'停止动画',exact:true}).getAttribute('aria-pressed'),'true');
  assert.equal(await page.getByRole('button',{name:'原形对照',exact:true}).getAttribute('aria-pressed'),'true');
  assert(Number(await page.getByRole('slider',{name:'变形倍率'}).inputValue())>=1);
  assert(await page.getByText('动画不代表振动或真实时间，颜色保持当前工况结果。',{exact:true}).isVisible());
  const frames=[];
  for(let i=0;i<3;i++){await page.waitForTimeout(600);frames.push(await page.locator('.canvas').screenshot({path:output+'/animation-'+i+'.png'}));}
  assert(!frames[0].equals(frames[1])&&!frames[1].equals(frames[2]),'Animation must visibly move');
  await page.getByRole('button',{name:'停止动画',exact:true}).click();
  await page.getByRole('button',{name:'真实比例',exact:true}).click();
  assert.equal(await page.getByRole('slider',{name:'变形倍率'}).inputValue(),'1');
  await page.getByRole('button',{name:'播放变形',exact:true}).click();
  assert.equal(await page.getByRole('slider',{name:'变形倍率'}).inputValue(),'1','Playing must preserve the explicit real scale');
  await page.getByRole('button',{name:'停止动画',exact:true}).click();
  await page.getByRole('spinbutton',{name:'输入变形倍率'}).fill('25');
  await page.getByRole('button',{name:'播放变形',exact:true}).click();
  assert.equal(await page.getByRole('slider',{name:'变形倍率'}).inputValue(),'25');
  await page.getByRole('button',{name:'未变形',exact:true}).click();
  assert.equal(await page.getByRole('slider',{name:'变形倍率'}).inputValue(),'0');
  await page.getByRole('button',{name:'自动放大',exact:true}).click();
  await page.getByRole('button',{name:'自动旋转',exact:true}).click();
  await page.getByRole('button',{name:'停止旋转',exact:true}).click();
  await page.getByRole('button',{name:'剖切',exact:true}).click();
  assert(await page.getByRole('button',{name:'播放变形',exact:true}).isDisabled());
  assert.equal(await page.getByRole('slider',{name:'变形倍率'}).inputValue(),'0');
  await page.getByRole('button',{name:'剖切',exact:true}).click();
  await page.getByRole('button',{name:'定位应力峰值',exact:true}).click();
  assert(await page.getByText('红点为积分点应力峰值，位置采用未变形坐标。',{exact:true}).isVisible());
  await page.getByRole('button',{name:'定位应力峰值',exact:true}).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'放大视图',exact:true}).waitFor();
  await page.screenshot({path:output+'/workbench.png',fullPage:true});
  for(const width of [1024,390]){
    await page.setViewportSize({width,height:900});
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:output+'/workbench-'+width+'.png',fullPage:true});
  }
  assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  console.log('PASS: initial/reloaded/project-switch model view; manual results; deformation animation, ghost, rotation, real scale, slice, peak, expanded view and responsive layout.');
} finally {await browser.close();}
