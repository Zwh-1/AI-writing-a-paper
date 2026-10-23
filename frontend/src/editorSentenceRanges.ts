export function codePointToUtf16(text:string,position:number):number {
  if(!Number.isInteger(position)||position<0)throw new Error('句子位置无效');
  const points=Array.from(text);if(position>points.length)throw new Error('句子位置超出正文');
  return points.slice(0,position).join('').length;
}
export function sentenceSelection(text:string,start:number,end:number){
  if(end<start)throw new Error('句子范围无效');
  return {from:codePointToUtf16(text,start),to:codePointToUtf16(text,end)};
}
