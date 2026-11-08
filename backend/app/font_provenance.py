"""Record actual font file identity; do not redistribute licensed font files."""
from pathlib import Path
from functools import lru_cache
import hashlib


@lru_cache(maxsize=64)
def _identity(path,size,mtime):
    data=Path(path).read_bytes();version=None;family=None
    from fontTools.ttLib import TTFont,TTCollection
    font=TTCollection(path).fonts[0] if Path(path).suffix.lower()=='.ttc' else TTFont(path)
    try:
        if 'name' in font:
            def name(identity):
                return next((r.toUnicode() for r in font['name'].names if r.nameID==identity),None)
            version=name(5);family=name(1)
    finally:font.close()
    return {'file_name':Path(path).name,'sha256':hashlib.sha256(data).hexdigest(),
            'bytes':len(data),'version':version,'family':family}


def identity(path):
    file=Path(path);stat=file.stat()
    return _identity(str(file.resolve()),stat.st_size,stat.st_mtime_ns)


def diagram_fonts():
    from .paper_visual_render import _font
    import PIL,reportlab
    return {'raster_measurement':identity(_font(19).path),'pillow_version':PIL.__version__,
            'svg':{'families':['SimSun','Microsoft YaHei','sans-serif'],'embedded':False,
                   'notice':'查看软件可能替换字体，需在目标软件核对'},
            'pdf':{'family':'STSong-Light','type':'predefined_CID_not_embedded',
                   'renderer':'ReportLab '+reportlab.Version,'font_file_sha256':None}}
