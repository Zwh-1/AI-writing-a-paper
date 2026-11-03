"""Portable HTML text extraction without an optional parser dependency."""
from html.parser import HTMLParser


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.hidden=[]

    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','template','head'):self.hidden.append(tag)
        elif not self.hidden and tag in ('p','div','section','article','h1','h2','h3','li','tr','br','hr'):self.parts.append('\n')

    def handle_endtag(self,tag):
        if self.hidden:
            if self.hidden[-1]==tag:self.hidden.pop()
        elif tag in ('p','div','section','article','h1','h2','h3','li','tr','td','th'):self.parts.append('\n')

    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def html_text(value):
    parser=_Text();parser.feed(value);parser.close()
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
