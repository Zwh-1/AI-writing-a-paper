import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const current=JSON.parse(fs.readFileSync(path.join(root,'frontend/package.json'),'utf8')).version;
if(process.argv.includes('--check')){
  const lock=JSON.parse(fs.readFileSync(path.join(root,'frontend/package-lock.json'),'utf8'));
  const backend=fs.readFileSync(path.join(root,'backend/app/config.py'),'utf8').match(/APP_VERSION\s*=\s*['"]([^'"]+)['"]/);
  const installer=fs.readFileSync(path.join(root,'deployment/desktop/windows.nsi'),'utf8').match(/!define VERSION "([^"]+)"/);
  if(!backend||!installer||[lock.version,lock.packages?.['']?.version,backend[1],installer[1]].some(value=>value!==current))throw new Error('Frontend, backend, lock file and installer versions must agree.');
  console.log(JSON.stringify({version:current,consistent:true}));process.exit(0);
}
const previous=process.argv.includes('--auto')?(()=>{try{return execFileSync('git',['describe','--tags','--abbrev=0','--match','v*'],{cwd:root,encoding:'utf8'}).trim();}catch{return '';}})():'';
let version=process.argv.find(value=>/^\d+\.\d+\.\d+$/.test(value));
if(process.argv.includes('--auto')){
  const args=['log','--format=%B',...(previous?[`${previous}..HEAD`]:[])];const text=execFileSync('git',args,{cwd:root,encoding:'utf8'});
  let parts=(previous?previous.slice(1):current).split('.').map(Number);
  if(/BREAKING CHANGE:|^[a-z]+(?:\([^)]*\))?!:/m.test(text))parts=[parts[0]+1,0,0];else if(/^feat(?:\([^)]*\))?:/m.test(text))parts=[parts[0],parts[1]+1,0];else if(/^(fix|perf)(?:\([^)]*\))?:/m.test(text))parts[2]++;else{console.log('No release-bearing conventional commits.');process.exit(0);}
  version=parts.join('.');
}
if(!version)throw new Error('Provide x.y.z or --auto.');
if(process.argv.includes('--dry-run')){console.log(JSON.stringify({current,next:version,dryRun:true}));process.exit(0);}
for(const file of ['frontend/package.json','frontend/package-lock.json']){const full=path.join(root,file),value=JSON.parse(fs.readFileSync(full,'utf8'));value.version=version;if(value.packages?.[''])value.packages[''].version=version;fs.writeFileSync(full,JSON.stringify(value,null,2)+'\n');}
const config=path.join(root,'backend/app/config.py');fs.writeFileSync(config,fs.readFileSync(config,'utf8').replace(/APP_VERSION = '[^']*'/,`APP_VERSION = '${version}'`));
const installer=path.join(root,'deployment/desktop/windows.nsi');
if(fs.existsSync(installer))fs.writeFileSync(installer,fs.readFileSync(installer,'utf8').replace(/!define VERSION "[^"]*"/,`!define VERSION "${version}"`));
if(process.env.GITHUB_OUTPUT)fs.appendFileSync(process.env.GITHUB_OUTPUT,`version=${version}\nreleased=true\n`);
console.log(version);
