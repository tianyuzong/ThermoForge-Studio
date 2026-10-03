import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const browser=await chromium.launch({headless:true,executablePath:process.env.STRENGTH_BROWSER_EXE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
const page=await browser.newPage({viewport:{width:1500,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
const root=process.env.STRENGTH_URL||'http://127.0.0.1:8876';
try{
 await page.goto(root);
 if(process.env.STRENGTH_TEST_PROJECT){await page.getByRole('combobox',{name:'选择项目',exact:true}).selectOption(process.env.STRENGTH_TEST_PROJECT);await page.getByRole('button',{name:'工况设置',exact:true}).click();}
 else await page.getByRole('button',{name:'双组件示例',exact:true}).click();
 await page.getByRole('heading',{name:'工况设置',exact:true}).waitFor();
 const projectId=await page.getByRole('combobox',{name:'选择项目',exact:true}).inputValue();
 await page.getByRole('button',{name:'组件材料',exact:true}).click();
 assert(await page.locator('.assigned-material').filter({hasText:'演示钢材（假设值）'}).isVisible());
 assert(await page.locator('.assigned-material').filter({hasText:'演示铝材（假设值）'}).isVisible());
 await page.getByRole('combobox',{name:'组件计算方式',exact:true}).selectOption('unconfigured');
 assert(await page.getByRole('button',{name:'运行工况',exact:true}).isDisabled());
 await page.getByRole('combobox',{name:'组件计算方式',exact:true}).selectOption('independent');
 await page.getByRole('button',{name:'保存工况',exact:true}).click();
 await page.getByRole('status').filter({hasText:'工况已保存'}).waitFor();
 assert.equal((await (await page.request.get(root+'/api/v1/jobs?project_id='+projectId)).json()).length,0);
 await page.getByRole('button',{name:'载荷',exact:true}).click();
 await page.getByRole('combobox',{name:'筛选工况组件',exact:true}).selectOption('2');
 assert.equal(await page.getByRole('textbox',{name:'载荷名称',exact:true}).count(),1);
 assert.equal(await page.getByRole('combobox',{name:'作用组件',exact:true}).inputValue(),'2');
 await page.screenshot({path:'../docs/component-case-editor.png',fullPage:true});
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByTitle('指定 组件 2 材料',{exact:true}).click();
 assert.equal(await page.getByRole('combobox',{name:'材料作用组件',exact:true}).inputValue(),'2');
 await page.screenshot({path:'../docs/component-material-editor.png',fullPage:true});
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 const patched=await page.request.patch(root+'/api/v1/projects/'+projectId,{data:{compute_backend:'cudss_cuda'}});assert(patched.ok());
 await page.reload();await page.getByRole('button',{name:'工况设置',exact:true}).click();
 const saved=await (await page.request.get(root+'/api/v1/projects/'+projectId+'/cases')).json();
 await page.getByRole('combobox',{name:'选择已保存工况',exact:true}).selectOption(saved[0].id);
 const response=page.waitForResponse(r=>r.url().endsWith('/projects/'+projectId+'/jobs')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'运行工况',exact:true}).click();const task=await (await response).json();assert(task.id,JSON.stringify(task));
 let result;for(let n=0;n<180;n++){result=await (await page.request.get(root+'/api/v1/agent/tasks/'+task.id)).json();if(['completed','failed','partial'].includes(result.status))break;await new Promise(r=>setTimeout(r,500));}
 assert.equal(result.status,'completed',JSON.stringify(result));const summary=result.jobs[0].summary;
 assert.equal(summary.components.length,2);assert.equal(summary.execution.backend,'cudss_cuda');assert(summary.balance.passed);
 await page.getByText('按组件评估 · 独立结构，互不传力',{exact:true}).waitFor();
 await page.screenshot({path:'../docs/component-workbench.png',fullPage:true});assert.deepEqual(errors,[]);
 await fs.writeFile('../.runtime/component-ui-result.json',JSON.stringify({project_id:projectId,task_id:task.id,job_id:result.jobs[0].id,components:summary.components},null,2));
 console.log('Component material selection, scoped loads, saved-case reload and real GPU solve passed.');
}finally{await browser.close();}
