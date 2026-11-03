import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=path.join(root,'frontend/node_modules/mathlive/fonts');
if(!fs.existsSync(source))throw new Error('Install frontend dependencies before preparing formula fonts.');
fs.cpSync(source,path.join(root,'frontend/public/mathlive-fonts'),{recursive:true});
