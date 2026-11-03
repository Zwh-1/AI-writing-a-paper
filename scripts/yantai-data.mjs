#!/usr/bin/env node
// Read-only workspace CLI. Uses the app session for HTTP or a validated offline archive.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const args=process.argv.slice(2),options={},positionals=[];
for(let i=0;i<args.length;i++){if(args[i].startsWith('--')){const key=args[i].slice(2);if(['raw','help'].includes(key))options[key]=true;else options[key]=args[++i];}else positionals.push(args[i]);}
if(options.help||!positionals.length){console.log('砚台工作区数据 CLI\n\n  summary\n  list knowledge|documents|notes|conversations|projects\n  get knowledge|documents|notes|conversations <id>\n  search <query>\n\n选项：--project <id> --server http://127.0.0.1:8765 --snapshot <备份.zip|数据目录> --field <字段路径> --raw');process.exit(0);}
try{
  const command=positionals[0];let value;
  if(!['summary','list','get','search'].includes(command))throw new Error('未知查询命令');
  if(options.snapshot){
    const python=process.env.PAPER_PYTHON||path.join(root,'.runtime/python/python.exe');
    const run=spawnSync(python,['-I','-X','utf8',path.join(root,'scripts/yantai_data_offline.py'),JSON.stringify({command,section:positionals[1],id:positionals[2],query:positionals.slice(1).join(' '),limit:Number(options.limit||10),project:options.project,snapshot:path.resolve(options.snapshot)})],{encoding:'utf8',windowsHide:true,maxBuffer:20*1024*1024});
    if(run.status!==0)throw new Error(run.stderr.trim()||'读取离线数据失败');value=JSON.parse(run.stdout);
  }else{
    const origin=String(options.server||process.env.PAPER_SERVER_URL||'http://127.0.0.1:8765').replace(/\/$/,'');const parsed=new URL(origin);
    if(!['127.0.0.1','localhost','[::1]'].includes(parsed.hostname))throw new Error('数据 CLI 当前仅连接本机工作台');
    const port=parsed.port||'80',candidates=[process.env.PAPER_SESSION_DIR&&path.join(process.env.PAPER_SESSION_DIR,`session-${port}.json`),path.join(root,`.runtime/session-${port}.json`),process.env.APPDATA&&path.join(process.env.APPDATA,`Yantai/runtime/session-${port}.json`)];const sessionFile=candidates.find(file=>file&&fs.existsSync(file));
    if(!sessionFile)throw new Error('未找到本机工作台会话，请先启动应用，或显式指定 --snapshot');
    const session=JSON.parse(fs.readFileSync(sessionFile,'utf8'));
    const response=await fetch(origin+'/api/v1/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:session.bootstrap_token})});
    if(!response.ok)throw new Error('本机工作台会话无效，请重启后再试');
    const cookie=response.headers.get('set-cookie')?.split(';')[0];const envelope=await response.json();const csrf=envelope.csrf;
    const request=async(route,body)=>{const response=await fetch(origin+route,{method:body?'POST':'GET',headers:{Cookie:cookie||'','X-CSRF-Token':csrf,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:data.error?.message||'数据查询失败');return data;};
    const projects=await request('/api/v1/projects');let pid=options.project;
    if(!pid){if(projects.length!==1&&!(command==='list'&&positionals[1]==='projects'))throw new Error('存在多个项目，请用 --project 指定目标');pid=projects[0]?.id;}
    if(command==='list'&&positionals[1]==='projects')value=projects;
    else{
      if(!projects.some(project=>project.id===pid))throw new Error('目标项目不存在');
      const route='/api/v1/projects/'+encodeURIComponent(pid);
      value=await request(route+'/workspace/query',{command,section:positionals[1]||'knowledge',id:positionals[2],query:positionals.slice(1).join(' '),limit:Number(options.limit||10)});
    }
  }
  if(options.field)for(const part of options.field.split('.'))value=value?.[part];
  if(value===undefined)throw new Error('所选字段不存在');
  process.stdout.write(options.raw&&typeof value==='string'?value+'\n':JSON.stringify(value,null,2)+'\n');
}catch(error){console.error(error.message);process.exitCode=1;}
