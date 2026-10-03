import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

// Read the existing model, exercise the form and validation, then discard.
// This test never saves a case, changes supports, or submits a solver job.
const url=process.env.STRENGTH_URL||'http://127.0.0.1:8876';
const browser=await chromium.launch({headless:true,...(process.env.STRENGTH_BROWSER_EXE?{executablePath:process.env.STRENGTH_BROWSER_EXE}:{})});
const page=await browser.newPage({viewport:{width:1500,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 const projects=await (await page.request.get(url+'/api/v1/projects')).json();
 const project=projects.find(p=>p.current_case&&(!process.env.STRENGTH_TEST_PROJECT||p.id===process.env.STRENGTH_TEST_PROJECT));
 assert(project,'Save a valid case before this form check');
 await page.addInitScript(id=>localStorage.setItem('strength-project',id),project.id);
 await page.goto(url);
 await page.getByRole('button',{name:'工况设置',exact:true}).click({timeout:30000});
 await page.getByRole('button',{name:'局部应力',exact:true}).click();
 const existing=await page.getByRole('textbox',{name:/局部应力名称/}).count();
 const i=existing+1;
 await page.getByRole('button',{name:'添加局部应力评估',exact:true}).click();
 await page.getByRole('textbox',{name:`局部应力名称 ${i}`,exact:true}).fill('界面校验选区（不保存）');
 const component=page.getByRole('combobox',{name:`局部应力组件 ${i}`,exact:true});
 const body=await component.locator('option').nth(1).getAttribute('value');
 await component.selectOption(body);
 const selection=page.getByRole('combobox',{name:`局部应力选区 ${i}`,exact:true});
 const name=await selection.locator('option').nth(1).textContent();
 await selection.selectOption({label:name});
 await page.getByRole('combobox',{name:`局部应力指标 ${i}`,exact:true}).selectOption('normal');
 let response=page.waitForResponse(r=>r.url().endsWith('/validate')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'检查配置',exact:true}).click();
 assert.equal((await response).status(),422);
 await page.getByRole('spinbutton',{name:`应力方向 ${i} X`,exact:true}).fill('1');
 response=page.waitForResponse(r=>r.url().endsWith('/validate')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'检查配置',exact:true}).click();
 const result=await response;assert(result.ok(),await result.text());
 const sent=result.request().postDataJSON().stress_regions.at(-1);
 assert.equal(sent.selection.body_id,Number(body));assert.deepEqual(sent.direction,[1,0,0]);
 if(process.env.STRENGTH_REGION_SCREENSHOT)await page.screenshot({path:process.env.STRENGTH_REGION_SCREENSHOT,fullPage:true});
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 const after=await (await page.request.get(url+'/api/v1/projects/'+project.id)).json();
 assert.deepEqual(after.current_case,project.current_case);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({regionForm:true,zeroDirectionRejected:true,validDirectionAccepted:true,projectUnchanged:true,pageErrors:errors}));
}finally{await browser.close();}
