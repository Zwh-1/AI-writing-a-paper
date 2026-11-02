import { apiUrl } from "../client";
import type { ReactNode } from "react";
import "./documentPreview.css";
type Row=Record<string,any>;
export default function DocumentPreview({document,sectionId,figures=[],assets=[],citations={}}:{document:Row;sectionId:string;figures?:Row[];assets?:Row[];citations?:Record<string,number>}){
  const label=(attrs:Row)=>{const matches=figures.filter(r=>r.node_id===attrs.targetId&&(!attrs.targetSectionId||r.section_id===attrs.targetSectionId));return matches.length===1?matches[0].label:"【图表引用待核对】";};
  const render=(node:Row,key:string):ReactNode=>{
    const a=node.attrs??{},children=(node.content??[]).map((c:Row,i:number)=>render(c,`${key}-${i}`));
    if(node.type==="text"){let text:ReactNode=node.text??"";for(const mark of node.marks??[]){if(mark.type==="bold")text=<strong>{text}</strong>;if(mark.type==="italic")text=<em>{text}</em>;if(mark.type==="underline")text=<u>{text}</u>;if(mark.type==="superscript")text=<sup>{text}</sup>;if(mark.type==="subscript")text=<sub>{text}</sub>;}return <span key={key}>{text}</span>;}
    if(node.type==="citation")return <sup key={key}>[{citations[a.citationId]??"引用待核对"}]</sup>;
    if(node.type==="crossReference")return <span key={key} className="preview-crossref">{label(a)}</span>;
    if(node.type==="hardBreak")return <br key={key}/>;
    if(node.type==="paragraph")return <p key={key}>{children.length?children:<br/>}</p>;
    if(node.type==="heading")return <h4 key={key}>{children}</h4>;
    if(node.type==="blockquote")return <blockquote key={key}>{children}</blockquote>;
    if(node.type==="bulletList")return <ul key={key}>{children}</ul>;
    if(node.type==="orderedList")return <ol key={key} start={a.start}>{children}</ol>;
    if(node.type==="listItem")return <li key={key}>{children}</li>;
    if(node.type==="tableRow")return <tr key={key}>{children}</tr>;
    if(node.type==="tableCell")return <td key={key} colSpan={a.colspan} rowSpan={a.rowspan}>{children}</td>;
    if(node.type==="tableHeader")return <th key={key} colSpan={a.colspan} rowSpan={a.rowspan}>{children}</th>;
    if(node.type==="table"){const row=figures.find(r=>r.section_id===sectionId&&r.node_id===a.nodeId);return <div className="preview-table" key={key}><table data-table-style={a.tableStyle}><caption>{row?.label} {a.caption||"【表题待补】"}</caption><tbody>{children}</tbody></table>{a.note&&<p className="preview-note">注：{a.note}</p>}</div>;}
    if(node.type==="figure"){const asset=assets.find(r=>r.id===a.artifactId),row=figures.find(r=>r.section_id===sectionId&&r.node_id===a.nodeId);return <figure key={key}>{asset?.sha256?<img alt={a.caption||asset.name} src={apiUrl(`/images/${asset.sha256}`)}/>:<p>【图片资源待核对】</p>}<figcaption>{row?.label} {a.caption||asset?.name}</figcaption></figure>;}
    if(node.type==="formula")return <div className="preview-formula" key={key}>{a.latex||a.expression||"公式原件将在文档中保留"}</div>;
    if(node.type==="footnote")return <sup title={a.noteText} key={key}>〔注〕</sup>;
    return <div key={key}>{children}</div>;
  };
  return <div className="document-preview">{render(document,"root")}</div>;
}
