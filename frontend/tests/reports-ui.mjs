import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const url=process.env.STRENGTH_URL||'http://127.0.0.1:8876';
const browser=await chromium.launch({headless:true,...(process.env.STRENGTH_BROWSER_EXE?{executablePath:process.env.STRENGTH_BROWSER_EXE}:{})});
const page=await browser.newPage({viewport:{width:1600,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 const jobs=await (await page.request.get(url+'/api/v1/jobs')).json();
 const job=jobs.find(j=>j.status==='completed'&&(!process.env.STRENGTH_REPORT_JOB||j.id===process.env.STRENGTH_REPORT_JOB));
 assert(job,'Run a real case before this report UI check');
 const evidence=await (await page.request.get(url+`/api/v1/jobs/${job.id}/report`)).json();
 assert(evidence.available&&evidence.figures.length>=7);
 await page.addInitScript(id=>localStorage.setItem('strength-project',id),job.project_id);
 await page.goto(url);
 await page.getByRole('button',{name:'报告图表',exact:true}).click({timeout:30000});
 const dialog=page.getByRole('dialog',{name:'图文分析报告'});
 await dialog.getByText('Ansys 同条件数值对照：尚未验证',{exact:true}).waitFor();
 await dialog.getByRole('button',{name:'等效应力',exact:true}).click();
 await dialog.locator('figure img').evaluate(async im=>{if(!im.complete)await new Promise((resolve,reject)=>{im.onload=resolve;im.onerror=reject;});});
 assert(await dialog.locator('figure img').evaluate(im=>im.naturalWidth>=1400));
 if(process.env.STRENGTH_REPORT_SCREENSHOT)await page.screenshot({path:process.env.STRENGTH_REPORT_SCREENSHOT,fullPage:true});
 await dialog.getByRole('button',{name:'关闭',exact:true}).click();
 const task=await (await page.request.get(url+`/api/v1/agent/tasks/${job.task_id}`)).json();
 assert(task.report_available);
 const pdf=await page.request.get(url+`/api/v1/agent/tasks/${job.task_id}/files/report.pdf`);
 assert(pdf.ok()&&(await pdf.body()).subarray(0,4).toString()==='%PDF');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({reportGallery:true,figureCount:evidence.figures.length,taskPdf:true,pageErrors:errors}));
}finally{await browser.close();}
