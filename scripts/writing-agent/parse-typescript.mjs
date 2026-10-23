import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
let ts;
try{ts=require('typescript');}catch{ts=require('../.runtime/writing-agent/typescript/lib/typescript.js');}
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const output={parser:'typescript-syntax.'+ts.version,files:[]};
for(const file of request.files){
  const source=ts.createSourceFile(file.path,file.text,ts.ScriptTarget.Latest,true,file.path.endsWith('tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
  const definitions=[],imports=[],calls=[];
  const line=node=>source.getLineAndCharacterOfPosition(node.getStart(source)).line+1;
  function walk(node){
    const name=node.name?.getText(source);
    if(name && (ts.isFunctionDeclaration(node)||ts.isClassDeclaration(node)||ts.isInterfaceDeclaration(node)||ts.isMethodDeclaration(node)||ts.isVariableDeclaration(node))){
      definitions.push({symbol:name,kind:ts.isClassDeclaration(node)?'class':ts.isInterfaceDeclaration(node)?'interface':ts.isVariableDeclaration(node)?'variable':'function',start:line(node),end:source.getLineAndCharacterOfPosition(node.end).line+1});
    }
    if(ts.isImportDeclaration(node)&&ts.isStringLiteral(node.moduleSpecifier)){
      const bindings=[];const clause=node.importClause;
      if(clause?.name)bindings.push({local:clause.name.text,imported:'default'});
      if(clause?.namedBindings&&ts.isNamedImports(clause.namedBindings))for(const spec of clause.namedBindings.elements)bindings.push({local:spec.name.text,imported:(spec.propertyName??spec.name).text});
      imports.push({target:node.moduleSpecifier.text,start:line(node),bindings});
    }
    if(ts.isCallExpression(node)){
      const target=node.expression.getText(source);
      if(target.length<160)calls.push({target,start:line(node)});
      if(ts.isPropertyAccessExpression(node.expression)&&['get','post','put','patch','delete','use'].includes(node.expression.name.text)&&node.arguments.length&&ts.isStringLiteral(node.arguments[0]))definitions.push({symbol:node.expression.name.text+' '+node.arguments[0].text,kind:'literal_route',start:line(node),end:source.getLineAndCharacterOfPosition(node.end).line+1});
    }
    ts.forEachChild(node,walk);
  }
  walk(source);
  output.files.push({path:file.path,definitions:definitions.slice(0,5000),imports:imports.slice(0,2000),calls:calls.slice(0,10000),truncated:definitions.length>5000||calls.length>10000,diagnostics:source.parseDiagnostics.map(x=>({start:x.start,message:ts.flattenDiagnosticMessageText(x.messageText,' ')}))});
}
process.stdout.write(JSON.stringify(output));
