"""Bounded one-shot PDF processing; no service, network call or local AI model."""
import json
import os
import sys


def main():
    if os.name == 'posix':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 12))
        resource.setrlimit(resource.RLIMIT_FSIZE, (55 * 1024 * 1024, 55 * 1024 * 1024))
    from pypdf import PdfReader, PdfWriter, filters
    from PIL import Image
    import warnings
    Image.MAX_IMAGE_PIXELS = int(os.environ.get('MAX_IMAGE_PIXELS', '20000000'))
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    for name in ('ZLIB_MAX_OUTPUT_LENGTH','LZW_MAX_OUTPUT_LENGTH','RUN_LENGTH_MAX_OUTPUT_LENGTH',
                 'JBIG2_MAX_OUTPUT_LENGTH','MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH'):
        if hasattr(filters, name):setattr(filters, name, 8 * 1024 * 1024)
    def reject_xfa(reader):
        form=reader.trailer['/Root'].get('/AcroForm')
        if form and '/XFA' in form.get_object():raise ValueError('XFA forms unsupported')
    action, path = sys.argv[1:3]
    if action == 'merge':
        import io
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        writer = PdfWriter()
        for name in sys.argv[3:]:
            with open(name, 'rb') as source:pdf = source.read(5) == b'%PDF-'
            if pdf:
                reader = PdfReader(name)
                reject_xfa(reader)
                if reader.is_encrypted or len(reader.pages) + len(writer.pages) > 100:raise ValueError()
                writer.append(reader)
            else:
                with Image.open(name, formats=('JPEG','PNG','WEBP')) as image:width,height = image.size
                page = io.BytesIO()
                c = canvas.Canvas(page, pagesize=(595,842), invariant=1)
                scale = min(555/width,802/height)
                c.drawImage(ImageReader(name),(595-width*scale)/2,(842-height*scale)/2,width=width*scale,height=height*scale)
                c.showPage();c.save();page.seek(0)
                writer.append(PdfReader(page))
        if not 1 <= len(writer.pages) <= 100:raise ValueError()
        writer.write(path)
        print('{}')
        return
    reader = PdfReader(path)
    reject_xfa(reader)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:raise ValueError()
    pages = []
    total = decoded = 0
    for page in reader.pages:
        contents = page.get_contents()
        if contents is not None:
            data = contents.get_data()
            decoded += len(data)
            if decoded > 32 * 1024 * 1024:raise ValueError()
        if action in ('text','layout'):
            text = page.extract_text(extraction_mode='layout' if action == 'layout' else 'plain') or ''
            total += len(text)
            if total > 250000:raise ValueError()
            pages.append(text)
    print(json.dumps({'pages': pages, 'count':len(reader.pages)}))


if __name__ == '__main__':
    try:main()
    except BaseException:sys.exit(1)
