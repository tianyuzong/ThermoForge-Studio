import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const evidence=JSON.parse(await fs.readFile('../.runtime/component-ui-result.json','utf8'));
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
const page=await browser.newPage({viewport:{width:1500,height:1000}});
try{
 await page.goto('http://127.0.0.1:8876');
 await page.getByRole('combobox',{name:'选择项目',exact:true}).selectOption(evidence.project_id);
 await page.locator('.component-item').last().waitFor();
 await page.locator('.run-item').first().click();await page.getByText('按组件评估 · 独立结构，互不传力',{exact:true}).waitFor();
 await page.screenshot({path:'../docs/component-workbench.png',fullPage:true});
 await page.getByRole('button',{name:'工况设置',exact:true}).click();
 await page.locator('.edit-card .row select').first().selectOption('pressure');
 await page.getByRole('combobox',{name:'作用组件',exact:true}).first().selectOption('2');
 const response=page.waitForResponse(r=>r.url().endsWith('/validate')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'检查配置',exact:true}).click();const checked=await response;
 assert(checked.ok(),await checked.text());
 const payload=checked.request().postDataJSON();assert.equal(payload.loads[0].selection.body_id,2);assert.equal(payload.loads[0].body_id,null);
 console.log('Changing load type and target component preserves the correct validated scope.');
}finally{await browser.close();}
