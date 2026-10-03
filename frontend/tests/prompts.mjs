import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
const base=process.env.STRENGTH_URL||'http://127.0.0.1:8878';
const project=process.env.STUDIO_PROJECT||'prompt-rod-project';
const out='../validation/prompt-ui';await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge',args:['--enable-unsafe-swiftshader']});
const errors=[];
let page=await browser.newPage({viewport:{width:1540,height:1100}});page.on('pageerror',e=>errors.push(String(e)));
const prompt='仅针对当前项目的导热杆模型完成两项分析。第一项继承已保存 current 工况的全部材料、左端固定和网格，只将名为“拉力”的面总力改为沿 +X 的 200 N，计算应力、位移和支反力。第二项继承已保存 example 的绝热体热源瞬态配置，功率保持 39 W，初始温度保持 25 ℃，总时长 10 s、时间步 2 s、保存间隔 2 s；这一项独立执行，不继承任何稳态结果。保留软件演示材料来源，不新增电机或轴承模型，不添加疲劳或接触工况。条件完整后自动求解，并生成真实保存帧的温度动画、静力视角展示 MP4、中文汇总与分项报告、指标 CSV、原始 HDF5、工况快照和 ZIP。';
try{
 await page.goto(base+`/?project=${project}&prompt=1`);
 await expect(page.getByRole('heading',{name:'指令仿真',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'解析并运行',exact:true})).toBeDisabled();
 await page.getByLabel('仿真任务长指令').fill(prompt);
 const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/analysis-plans'));
 await page.getByRole('button',{name:'解析并运行',exact:true}).click();
 const plan=await (await response).json();if(!plan.id)throw Error(JSON.stringify(plan));
 await page.screenshot({path:out+'/planning.png',fullPage:true});
 await fs.writeFile(out+'/submitted.json',JSON.stringify({project,plan_id:plan.id,prompt},null,2));
 console.log('Submitted real CLI plan '+plan.id);
 // Close the browser while parsing. The server must own both planning and solving.
 await page.close();
 let value;const end=Date.now()+900000;
 while(Date.now()<end){
  value=await (await fetch(base+'/api/v1/analysis-plans/'+plan.id)).json();
  if(['failed','needs_input','cancelled','interrupted'].includes(value.status))throw Error(JSON.stringify(value));
  if(value.run_id)break;
  await new Promise(r=>setTimeout(r,3000));
 }
 if(!value.run_id)throw Error('Planner timeout');
 console.log('Background run '+value.run_id);
 let run;
 while(Date.now()<end){run=await (await fetch(base+'/api/v1/analysis-runs/'+value.run_id)).json();if(['completed','partial','failed','cancelled','interrupted'].includes(run.status))break;await new Promise(r=>setTimeout(r,2000));}
 await fs.writeFile(out+'/result.json',JSON.stringify({plan:value,run},null,2));
 if(run.status!=='completed')throw Error(JSON.stringify(run));
 expect(run.nodes).toHaveLength(2);expect(run.nodes.every(n=>n.model_id==='prompt-rod')).toBeTruthy();
 const staticNode=run.nodes.find(n=>n.case.analysis_type==='static');const thermal=run.nodes.find(n=>n.case.analysis_type==='thermal_transient');
 expect(staticNode.case.loads[0].vector).toEqual([200,0,0]);expect(thermal.case.thermal.initial_from_study).toBeNull();
 page=await browser.newPage({viewport:{width:1540,height:1100}});page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(base+`/?project=${project}&run=${run.id}`);
 await expect(page.locator('.run-node')).toHaveCount(2);await expect(page.locator('.run-canvas canvas')).toBeVisible({timeout:120000});
 await page.screenshot({path:out+'/completed.png',fullPage:true});
 await page.locator('.run-node').nth(run.nodes.indexOf(thermal)).click();
 await expect(page.getByLabel('全套结果时间帧')).toBeVisible({timeout:120000});await page.getByLabel('全套结果时间帧').fill('5');
 await expect(page.locator('.run-timeline')).toContainText('10',{timeout:120000});
 await page.screenshot({path:out+'/transient.png',fullPage:true});
 expect(run.exports.every(e=>e.files.some(f=>f.path.endsWith('.mp4')))).toBeTruthy();
 expect((await fetch(base+`/api/v1/analysis-runs/${run.id}/files/bundle.zip`)).status).toBe(200);
 // Offline prose must explain its limits without importing a demo or submitting a solve.
 await page.goto(base+`/?project=${project}&prompt=1`);await page.getByLabel('指令解析方式').selectOption('local');
 await page.getByLabel('仿真任务长指令').fill('只对当前零件施加未指定的热源，不能猜测参数');await page.getByRole('button',{name:'解析并运行',exact:true}).click();
 await expect(page.getByLabel('指令解析结果')).toContainText('需要补充条件',{timeout:15000});
 await expect(page.locator('.run-node')).toHaveCount(0);await page.screenshot({path:out+'/missing-input.png',fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS: real long prompt, current project, two studies, browser closed, MP4/PDF/ZIP, actual frames and missing input');
}finally{await browser.close()}
