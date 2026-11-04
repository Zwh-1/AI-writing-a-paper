"""Embed an SVG diagram with its PNG fallback for older Word/PDF engines."""
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from lxml import etree


def attach_svg(doc,shape,data):
    root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True))
    if root.tag!='{http://www.w3.org/2000/svg}svg':raise ValueError('矢量图不是SVG文档')
    if root.xpath('//*[local-name()="script"]') or root.xpath('//@*[local-name()="href" and not(starts-with(.,"#"))]'):
        raise ValueError('SVG不得包含脚本或外部资源')
    package=doc.part.package
    part=Part(package.next_partname('/word/media/vector%d.svg'),'image/svg+xml',data,package)
    relationship=doc.part.relate_to(part,RT.IMAGE)
    blip=shape._inline.xpath('.//a:blip')[0]
    extensions=blip.find(qn('a:extLst'))
    if extensions is None:extensions=OxmlElement('a:extLst');blip.append(extensions)
    extension=OxmlElement('a:ext');extension.set('uri','{96DAC541-7B7A-43D3-8B79-37D633B846F1}')
    svg=etree.Element('{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip',nsmap={'asvg':'http://schemas.microsoft.com/office/drawing/2016/SVG/main'})
    svg.set(qn('r:embed'),relationship);extension.append(svg);extensions.append(extension)
