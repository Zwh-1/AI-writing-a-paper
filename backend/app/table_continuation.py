"""Explicit Word page boundaries and actual PDF continuation captions."""
from copy import deepcopy
from fastapi import HTTPException


def validate_breaks(rows,breaks):
    if not isinstance(breaks,list) or any(type(x) is not int for x in breaks) or sorted(set(breaks))!=breaks:
        raise HTTPException(422,'续表分页行须为递增且不重复的整数')
    headers=0
    for row in rows:
        if row.get('content') and all(c.get('type')=='tableHeader' for c in row['content']):headers+=1
        else:break
    if breaks and (not headers or any(x<2 or x>len(rows)-headers for x in breaks)):raise HTTPException(422,'续表须有连续表头，分页行是从1开始的数据行，新页从该行开始')
    positions=[headers+x-1 for x in breaks]
    for i,row in enumerate(rows):
        for cell in row.get('content',[]):
            end=i+max(1,int(cell.get('attrs',{}).get('rowspan',1)))
            if any(i<p<end for p in positions):raise HTTPException(422,'续表分页不能穿过纵向合并单元格')
    return headers,positions


def split_word(doc,table,source_rows,breaks,caption):
    """Honor author-selected data row boundaries; never guess Word pagination."""
    from docx.table import Table
    from docx.oxml.ns import qn
    from docx.enum.text import WD_BREAK
    from .paper_tables import add_caption
    headers,positions=validate_breaks(source_rows,breaks)
    if not positions:return table
    original=deepcopy(table._tbl);rows=list(original.findall(qn('w:tr')))
    boundaries=[headers,*positions,len(rows)];last=table
    for index,(start,end) in enumerate(zip(boundaries,boundaries[1:])):
        element=deepcopy(original)
        for row in list(element.findall(qn('w:tr'))):element.remove(row)
        for row in [*rows[:headers],*rows[start:end]]:element.append(deepcopy(row))
        if index==0:
            table._tbl.getparent().replace(table._tbl,element);last=Table(element,table._parent)
        else:
            page=doc.add_paragraph();page.add_run().add_break(WD_BREAK.PAGE)
            label=add_caption(doc,caption+'（续）')
            last._tbl.addnext(page._p);page._p.addnext(label._p);label._p.addnext(element)
            last=Table(element,table._parent)
    return last


from reportlab.platypus import Table,Paragraph


class ContinuedTable(Table):
    """Reserve and draw the continuation label on each actual split fragment."""
    continuation=False
    continuation_caption=''
    caption_style=None

    def _caption(self,width):
        if not self.continuation or not self.continuation_caption:return None,0
        paragraph=Paragraph(self.continuation_caption+'（续）',self.caption_style)
        _,height=paragraph.wrap(width,10000)
        return paragraph,height+6

    def wrap(self,availWidth,availHeight):
        width,height=super().wrap(availWidth,availHeight)
        return width,height+self._caption(width)[1]

    def split(self,availWidth,availHeight):
        parts=super().split(availWidth,availHeight-self._caption(availWidth)[1])
        for index,part in enumerate(parts):
            if isinstance(part,ContinuedTable):
                part.continuation=self.continuation or index>0
                part.continuation_caption=self.continuation_caption;part.caption_style=self.caption_style
        return parts

    def drawOn(self,canvas,x,y,_sW=0):
        paragraph,height=self._caption(self._width)
        if paragraph:
            offset=_sW/2 if self.hAlign in ('CENTER',1) else _sW if self.hAlign in ('RIGHT',2) else 0
            paragraph.drawOn(canvas,x+offset,y+self._height+6)
        return super().drawOn(canvas,x,y,_sW)
